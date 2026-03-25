export type SearchSourceName = "WebOfScience" | "Elsevier" | "Springer" | "Crossref" | "PubMed";

export interface PaperMetadata {
    title: string;
    doi: string;
    abstract?: string;
    publisher?: string;
    authors?: string[];
    year?: string;
    url?: string;
    source: string;
}

export type SearchSourceState = Record<string, unknown>;

export interface SearchSourcePage {
    papers: PaperMetadata[];
    nextState: SearchSourceState;
    exhausted: boolean;
    warnings?: string[];
}

export interface SearchSourceAdapter {
    initializeState: (params: { limit: number; now: Date }) => SearchSourceState;
    fetchPage: (params: {
        query: string;
        limit: number;
        state: SearchSourceState;
        now: Date;
    }) => Promise<SearchSourcePage>;
}

export interface SearchContinuationTokenData {
    version: 1;
    query: string;
    normalized_query: string;
    active_sources: SearchSourceName[];
    source_states: Partial<Record<SearchSourceName, SearchSourceState>>;
    exhausted_sources: SearchSourceName[];
    seen_dois: string[];
    issued_at: string;
}

export interface RunResumableSearchParams {
    query: string;
    limit: number;
    continuationToken?: string;
    searchOrder: SearchSourceName[];
    searchEnabled?: Partial<Record<SearchSourceName, boolean>>;
    adapters: Partial<Record<SearchSourceName, SearchSourceAdapter>>;
    now?: () => Date;
    maxPageFetchesPerSourcePerCall?: number;
    crossrefCursorTtlMs?: number;
}

export interface ResumableSearchResult {
    query: string;
    limit: number;
    results: PaperMetadata[];
    hasMore: boolean;
    exhaustedSources: SearchSourceName[];
    nextContinuationToken?: string;
    warnings: string[];
    continuationApplied: boolean;
}

const DEFAULT_MAX_PAGE_FETCHES_PER_SOURCE_PER_CALL = 8;
const DEFAULT_CROSSREF_CURSOR_TTL_MS = 5 * 60 * 1000;

function normalizeQuery(query: string): string {
    return query.trim().replace(/\s+/g, " ").toLowerCase();
}

function normalizeDoi(doi: string): string {
    return doi.trim().toLowerCase();
}

function stableStringify(value: unknown): string {
    return JSON.stringify(value, Object.keys(value as Record<string, unknown>).sort());
}

function deepEqualStates(a: SearchSourceState, b: SearchSourceState): boolean {
    return stableStringify(a) === stableStringify(b);
}

function activeSourceOrder(
    searchOrder: SearchSourceName[],
    searchEnabled: Partial<Record<SearchSourceName, boolean>> | undefined,
    adapters: Partial<Record<SearchSourceName, SearchSourceAdapter>>
): SearchSourceName[] {
    return searchOrder.filter((sourceName) => {
        if (searchEnabled?.[sourceName] === false) return false;
        return !!adapters[sourceName];
    });
}

export function encodeSearchContinuationToken(data: SearchContinuationTokenData): string {
    return Buffer.from(JSON.stringify(data), "utf-8").toString("base64url");
}

export function decodeSearchContinuationToken(token: string): SearchContinuationTokenData {
    const decodedText = Buffer.from(token, "base64url").toString("utf-8");
    const parsed = JSON.parse(decodedText);

    if (parsed?.version !== 1) {
        throw new Error("Unsupported continuation_token version.");
    }
    if (typeof parsed?.query !== "string" || typeof parsed?.normalized_query !== "string") {
        throw new Error("Invalid continuation_token payload.");
    }
    if (!Array.isArray(parsed?.active_sources) || !Array.isArray(parsed?.exhausted_sources) || !Array.isArray(parsed?.seen_dois)) {
        throw new Error("Invalid continuation_token payload.");
    }

    return parsed as SearchContinuationTokenData;
}

function createInitialContinuationData(params: {
    query: string;
    limit: number;
    activeSources: SearchSourceName[];
    adapters: Partial<Record<SearchSourceName, SearchSourceAdapter>>;
    now: Date;
}): SearchContinuationTokenData {
    const sourceStates: Partial<Record<SearchSourceName, SearchSourceState>> = {};

    for (const sourceName of params.activeSources) {
        const adapter = params.adapters[sourceName];
        if (!adapter) continue;
        sourceStates[sourceName] = adapter.initializeState({ limit: params.limit, now: params.now });
    }

    return {
        version: 1,
        query: params.query,
        normalized_query: normalizeQuery(params.query),
        active_sources: params.activeSources,
        source_states: sourceStates,
        exhausted_sources: [],
        seen_dois: [],
        issued_at: params.now.toISOString()
    };
}

function prepareContinuationData(params: {
    query: string;
    limit: number;
    continuationToken?: string;
    searchOrder: SearchSourceName[];
    searchEnabled?: Partial<Record<SearchSourceName, boolean>>;
    adapters: Partial<Record<SearchSourceName, SearchSourceAdapter>>;
    now: Date;
}): { data: SearchContinuationTokenData; continuationApplied: boolean } {
    if (!params.continuationToken) {
        const activeSources = activeSourceOrder(params.searchOrder, params.searchEnabled, params.adapters);
        return {
            data: createInitialContinuationData({
                query: params.query,
                limit: params.limit,
                activeSources,
                adapters: params.adapters,
                now: params.now
            }),
            continuationApplied: false
        };
    }

    const decoded = decodeSearchContinuationToken(params.continuationToken);
    if (decoded.normalized_query !== normalizeQuery(params.query)) {
        throw new Error("continuation_token does not match the provided query.");
    }

    return {
        data: decoded,
        continuationApplied: true
    };
}

function refreshExpiredCrossrefState(params: {
    sourceName: SearchSourceName;
    sourceState: SearchSourceState;
    issuedAt: string;
    now: Date;
    limit: number;
    crossrefCursorTtlMs: number;
}): { state: SearchSourceState; warnings: string[] } {
    if (params.sourceName !== "Crossref") {
        return { state: params.sourceState, warnings: [] };
    }

    const cursor = typeof params.sourceState.cursor === "string" ? params.sourceState.cursor : "*";
    if (cursor === "*") {
        return { state: params.sourceState, warnings: [] };
    }

    const issuedAtMs = Date.parse(params.issuedAt);
    if (!Number.isFinite(issuedAtMs)) {
        return { state: params.sourceState, warnings: [] };
    }

    if (params.now.getTime() - issuedAtMs < params.crossrefCursorTtlMs) {
        return { state: params.sourceState, warnings: [] };
    }

    return {
        state: {
            cursor: "*",
            rows: typeof params.sourceState.rows === "number" ? params.sourceState.rows : params.limit,
            pagesFetched: 0,
            cursorIssuedAt: params.now.toISOString()
        },
        warnings: [
            "Crossref continuation cursor expired; restarting that source from the beginning and skipping already-seen DOIs."
        ]
    };
}

function upsertBatchResult(batchMap: Map<string, PaperMetadata>, paper: PaperMetadata): void {
    const doi = normalizeDoi(paper.doi);
    const existing = batchMap.get(doi);
    if (!existing || (!existing.abstract && paper.abstract)) {
        batchMap.set(doi, paper);
    }
}

export async function runResumableSearch(params: RunResumableSearchParams): Promise<ResumableSearchResult> {
    const now = params.now ? params.now() : new Date();
    const limit = Number.isFinite(params.limit) && params.limit > 0 ? Math.floor(params.limit) : 15;
    const maxPageFetchesPerSourcePerCall = params.maxPageFetchesPerSourcePerCall || DEFAULT_MAX_PAGE_FETCHES_PER_SOURCE_PER_CALL;
    const crossrefCursorTtlMs = params.crossrefCursorTtlMs || DEFAULT_CROSSREF_CURSOR_TTL_MS;

    const prepared = prepareContinuationData({
        query: params.query,
        limit,
        continuationToken: params.continuationToken,
        searchOrder: params.searchOrder,
        searchEnabled: params.searchEnabled,
        adapters: params.adapters,
        now
    });

    const continuationData = prepared.data;
    const warnings: string[] = [];
    const exhaustedSources = new Set<SearchSourceName>(continuationData.exhausted_sources);
    const seenDois = new Set<string>(continuationData.seen_dois.map((doi) => normalizeDoi(doi)));
    const batchMap = new Map<string, PaperMetadata>();

    for (const sourceName of continuationData.active_sources) {
        if (batchMap.size >= limit) break;
        if (exhaustedSources.has(sourceName)) continue;

        const adapter = params.adapters[sourceName];
        if (!adapter) {
            warnings.push(`${sourceName} continuation state exists, but no adapter is currently available. Marking it exhausted.`);
            exhaustedSources.add(sourceName);
            continue;
        }

        let currentState = continuationData.source_states[sourceName] || adapter.initializeState({ limit, now });
        const preparedState = refreshExpiredCrossrefState({
            sourceName,
            sourceState: currentState,
            issuedAt: continuationData.issued_at,
            now,
            limit,
            crossrefCursorTtlMs
        });
        currentState = preparedState.state;
        warnings.push(...preparedState.warnings);

        let pageFetches = 0;
        while (batchMap.size < limit && pageFetches < maxPageFetchesPerSourcePerCall) {
            pageFetches += 1;

            const page = await adapter.fetchPage({
                query: continuationData.query,
                limit,
                state: currentState,
                now
            });

            warnings.push(...(page.warnings || []));

            let newResultsFromPage = 0;
            for (const paper of page.papers) {
                if (!paper.doi) continue;

                const normalizedDoi = normalizeDoi(paper.doi);
                if (!normalizedDoi) continue;

                if (batchMap.has(normalizedDoi)) {
                    upsertBatchResult(batchMap, paper);
                    continue;
                }

                if (seenDois.has(normalizedDoi)) {
                    continue;
                }

                upsertBatchResult(batchMap, paper);
                seenDois.add(normalizedDoi);
                newResultsFromPage += 1;

                if (batchMap.size >= limit) break;
            }

            const nextState = page.nextState;
            const stateAdvanced = !deepEqualStates(currentState, nextState);
            currentState = nextState;
            continuationData.source_states[sourceName] = currentState;

            if (page.exhausted) {
                exhaustedSources.add(sourceName);
                break;
            }

            if (newResultsFromPage > 0) {
                break;
            }

            if (!stateAdvanced) {
                warnings.push(`${sourceName} did not advance its continuation state and yielded no new papers; marking it exhausted to avoid repeat loops.`);
                exhaustedSources.add(sourceName);
                break;
            }
        }
    }

    continuationData.seen_dois = Array.from(seenDois);
    continuationData.exhausted_sources = Array.from(exhaustedSources);
    continuationData.issued_at = now.toISOString();

    const hasMore = continuationData.active_sources.some((sourceName) => !exhaustedSources.has(sourceName));
    const results = Array.from(batchMap.values()).slice(0, limit);

    return {
        query: continuationData.query,
        limit,
        results,
        hasMore,
        exhaustedSources: Array.from(exhaustedSources),
        nextContinuationToken: hasMore ? encodeSearchContinuationToken(continuationData) : undefined,
        warnings,
        continuationApplied: prepared.continuationApplied
    };
}
