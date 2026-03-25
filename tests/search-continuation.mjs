import assert from 'node:assert/strict';
import { decodeSearchContinuationToken, runResumableSearch } from '../dist/resumable-search.js';

function paper(doi, overrides = {}) {
    return {
        title: `Paper ${doi}`,
        doi,
        source: overrides.source || 'Fixture Source',
        ...overrides
    };
}

function createPagedAdapter(pages, stateFactory = (index) => ({ page: index })) {
    return {
        initializeState: () => stateFactory(0),
        async fetchPage({ state }) {
            const pageIndex = Number(state.page || 0);
            const page = pages[pageIndex] || [];
            const nextIndex = pageIndex + 1;
            return {
                papers: page,
                nextState: stateFactory(nextIndex),
                exhausted: nextIndex >= pages.length
            };
        }
    };
}

async function testContinuationWithoutDuplicates() {
    const adapters = {
        Elsevier: createPagedAdapter([
            [paper('10.1/a', { abstract: undefined, source: 'Elsevier (Scopus)' }), paper('10.1/b', { source: 'Elsevier (Scopus)' })],
            [paper('10.1/c', { source: 'Elsevier (Scopus)' })]
        ]),
        Crossref: createPagedAdapter([
            [paper('10.1/a', { abstract: 'better abstract', source: 'Crossref' }), paper('10.1/d', { source: 'Crossref' })],
            [paper('10.1/e', { source: 'Crossref' })]
        ])
    };

    const first = await runResumableSearch({
        query: 'elastic metamaterial',
        limit: 3,
        searchOrder: ['Elsevier', 'Crossref'],
        adapters
    });

    assert.equal(first.results.length, 3, 'first batch should fill requested limit');
    assert.equal(first.hasMore, true, 'first batch should advertise more results');
    assert.equal(first.results[0].doi, '10.1/a');
    assert.equal(first.results[0].abstract, 'better abstract', 'same-call duplicates should be upgraded to the richer abstract');
    assert(first.nextContinuationToken, 'first batch should return a continuation token');

    const second = await runResumableSearch({
        query: 'elastic metamaterial',
        limit: 2,
        continuationToken: first.nextContinuationToken,
        searchOrder: ['Elsevier', 'Crossref'],
        adapters
    });

    assert.deepEqual(second.results.map((item) => item.doi), ['10.1/c', '10.1/e']);
    assert.equal(second.hasMore, false, 'second batch should exhaust both sources');
    assert.equal(second.nextContinuationToken, undefined, 'final batch should not return a continuation token');
  }

async function testQueryMismatchRejected() {
    const adapters = {
        Elsevier: createPagedAdapter([
            [paper('10.2/a')],
            [paper('10.2/b')]
        ])
    };

    const first = await runResumableSearch({
        query: 'query one',
        limit: 1,
        searchOrder: ['Elsevier'],
        adapters
    });
    assert(first.nextContinuationToken, 'mismatch test requires a continuation token');

    await assert.rejects(
        () => runResumableSearch({
            query: 'query two',
            limit: 1,
            continuationToken: first.nextContinuationToken,
            searchOrder: ['Elsevier'],
            adapters
        }),
        /does not match/
    );
}

async function testStuckSourceMarkedExhausted() {
    const adapters = {
        Springer: {
            initializeState: () => ({ stuck: true }),
            async fetchPage({ state }) {
                return {
                    papers: [],
                    nextState: state,
                    exhausted: false
                };
            }
        }
    };

    const result = await runResumableSearch({
        query: 'stuck source',
        limit: 2,
        searchOrder: ['Springer'],
        adapters
    });

    assert.equal(result.results.length, 0);
    assert.equal(result.hasMore, false, 'non-advancing empty sources should be marked exhausted');
    assert(result.warnings.some((warning) => warning.includes('did not advance')), 'should report why the source was exhausted');
}

async function testExpiredCrossrefCursorReplaysSafely() {
    const start = new Date('2026-03-25T00:00:00.000Z');
    const adapters = {
        Crossref: {
            initializeState: ({ limit, now }) => ({
                cursor: '*',
                rows: limit,
                pagesFetched: 0,
                cursorIssuedAt: now.toISOString()
            }),
            async fetchPage({ state, now }) {
                const cursor = state.cursor || '*';
                if (cursor === '*') {
                    return {
                        papers: [paper('10.3/a', { source: 'Crossref' })],
                        nextState: { cursor: 'cursor-1', rows: 1, pagesFetched: 1, cursorIssuedAt: now.toISOString() },
                        exhausted: false
                    };
                }

                return {
                    papers: [paper('10.3/b', { source: 'Crossref' })],
                    nextState: { cursor: 'cursor-2', rows: 1, pagesFetched: 2, cursorIssuedAt: now.toISOString() },
                    exhausted: true
                };
            }
        }
    };

    const first = await runResumableSearch({
        query: 'crossref cursor test',
        limit: 1,
        searchOrder: ['Crossref'],
        adapters,
        now: () => start
    });

    const decoded = decodeSearchContinuationToken(first.nextContinuationToken);
    assert.equal(decoded.source_states.Crossref.cursor, 'cursor-1');

    const second = await runResumableSearch({
        query: 'crossref cursor test',
        limit: 1,
        continuationToken: first.nextContinuationToken,
        searchOrder: ['Crossref'],
        adapters,
        now: () => new Date(start.getTime() + 6 * 60 * 1000)
    });

    assert.deepEqual(second.results.map((item) => item.doi), ['10.3/b']);
    assert(second.warnings.some((warning) => warning.includes('cursor expired')), 'should warn when replaying after cursor expiry');
}

await testContinuationWithoutDuplicates();
await testQueryMismatchRejected();
await testStuckSourceMarkedExhausted();
await testExpiredCrossrefCursorReplaysSafely();

console.log('search-continuation tests passed.');
