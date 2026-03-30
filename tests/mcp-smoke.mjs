/**
 * MCP Smoke Test — uses MCP SDK client to talk to GRaDOS server.
 * Usage: node tests/mcp-smoke.mjs
 */
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { existsSync, statSync } from 'fs';
import { join } from 'path';

async function test() {
    console.log('=== GRaDOS MCP Smoke Test ===\n');

    // 1. Connect
    console.log('1. Starting server & connecting...');
    const transport = new StdioClientTransport({
        command: 'node',
        args: ['dist/index.js'],
        cwd: process.cwd(),
    });
    const client = new Client({ name: 'smoke-test', version: '1.0.0' });
    await client.connect(transport);
    console.log('   ✅ Connected\n');

    // 2. List tools
    console.log('2. Listing tools...');
    const { tools } = await client.listTools();
    const toolNames = tools.map(t => t.name);
    console.log(`   Tools: ${toolNames.join(', ')}\n`);

    console.log('   Listing resources...');
    const { resources } = await client.listResources();
    const resourceUris = resources.map(r => r.uri);
    console.log(`   Resources: ${resourceUris.join(', ')}`);

    const { resourceTemplates } = await client.listResourceTemplates();
    const templateUris = resourceTemplates.map(t => t.uriTemplate);
    console.log(`   Resource templates: ${templateUris.join(', ')}\n`);

    console.log('2b. Searching saved papers (local library)...');
    const savedSearchResult = await client.callTool({
        name: 'search_saved_papers',
        arguments: { query: 'elastic metamaterial', limit: 3 }
    });
    const savedSearchStructured = savedSearchResult.structuredContent;
    console.log(`   retrieval_mode: ${savedSearchStructured?.retrieval_mode || '(none)'}`);
    console.log(`   paper hits: ${savedSearchStructured?.papers?.length || 0}\n`);

    // 3. Search
    console.log('3. Searching "elastic metamaterial" (limit 5)...');
    const searchResult = await client.callTool({
        name: 'search_academic_papers',
        arguments: { query: 'elastic metamaterial', limit: 5 }
    });
    const searchText = searchResult.content?.[0]?.text || '';
    console.log(`   Result length: ${searchText.length} chars`);

    // Extract DOIs and titles
    const doiRegex = /\*\*DOI:\*\*\s*(.+)/g;
    const dois = [];
    let m;
    while ((m = doiRegex.exec(searchText)) !== null) {
        dois.push(m[1].trim());
    }
    console.log(`   Found ${dois.length} DOIs: ${dois.slice(0, 3).join(', ')}${dois.length > 3 ? '...' : ''}`);

    const titleRegex = /### \d+\. (.+)/;
    const titleMatch = searchText.match(titleRegex);
    const firstTitle = titleMatch ? titleMatch[1].trim() : undefined;
    console.log(`   First title: ${firstTitle || '(none)'}\n`);

    if (dois.length === 0) {
        console.log('   ❌ No DOIs found, cannot test extraction.');
        await client.close();
        process.exit(1);
    }

    // 4. Extract full text
    const testDoi = dois[0];
    console.log(`4. Extracting full text: ${testDoi}...`);
    const extractResult = await client.callTool({
        name: 'extract_paper_full_text',
        arguments: { doi: testDoi, expected_title: firstTitle }
    });
    const extractText = extractResult.content?.[0]?.text || '';
    const extractSummary = extractResult.structuredContent;
    const isError = extractResult.isError || false;
    console.log(`   isError: ${isError}`);
    console.log(`   Length: ${extractText.length} chars`);
    if (extractText.length > 0) {
        console.log(`   Preview: ${extractText.substring(0, 200).replace(/\n/g, ' ')}...`);
    }
    if (!isError && extractSummary) {
        console.log(`   kind: ${extractSummary.kind}`);
        console.log(`   canonical_uri: ${extractSummary.canonical_uri}`);
        console.log(`   preview_not_citable: ${extractSummary.preview_not_citable}`);
    }
    console.log();

    // 5. Check file storage
    console.log('5. Checking file storage...');
    const safeDoi = testDoi.replace(/[^a-z0-9]/gi, '_');
    const mdFile = join(process.cwd(), 'markdown', `${safeDoi}.md`);
    const pdfFile = join(process.cwd(), 'downloads', `${safeDoi}.pdf`);

    const mdExists = existsSync(mdFile);
    const pdfExists = existsSync(pdfFile);
    console.log(`   markdown/${safeDoi}.md: ${mdExists ? '✅ ' + statSync(mdFile).size + ' bytes' : '⚠️  not found'}`);
    console.log(`   downloads/${safeDoi}.pdf: ${pdfExists ? '✅ ' + statSync(pdfFile).size + ' bytes' : '⚠️  not found (text-only source)'}`);
    console.log();

    console.log('5b. Reading paper resources (if available)...');
    let readOk = false;
    if (mdExists) {
        const readResult = await client.callTool({
            name: 'read_saved_paper',
            arguments: { safe_doi: safeDoi, max_paragraphs: 2 }
        });
        const readStructured = readResult.structuredContent;
        readOk = !readResult.isError && readStructured?.kind === 'paper_read_result';
        console.log(`   read_saved_paper: ${readOk ? '✅' : '⚠️'} ${readStructured?.canonical_uri || ''}`);

        const paperResource = await client.readResource({ uri: `grados://papers/${safeDoi}` });
        const paperResourceText = paperResource.contents?.[0]?.text || '';
        console.log(`   grados://papers/${safeDoi}: ${paperResourceText.length > 0 ? '✅' : '⚠️'} (${paperResourceText.length} chars)`);
    } else {
        console.log('   Skipped: no saved paper file to read back.');
    }
    console.log();

    // 6. Zotero (no API key → should fail gracefully)
    console.log('6. Testing save_paper_to_zotero (expect graceful failure)...');
    const zoteroResult = await client.callTool({
        name: 'save_paper_to_zotero',
        arguments: {
            doi: testDoi,
            title: firstTitle || 'Test Paper',
            authors: ['Test Author'],
            tags: ['elastic metamaterial']
        }
    });
    const zoteroText = zoteroResult.content?.[0]?.text || '';
    const zoteroIsError = zoteroResult.isError || false;
    console.log(`   isError: ${zoteroIsError}`);
    console.log(`   Response: ${zoteroText}\n`);

    // Summary
    console.log('=== Summary ===');
    console.log(`   Tools registered:     ${toolNames.length === 6 ? '✅' : '❌'} (${toolNames.length}/6)`);
    console.log(`   Resources listed:     ${resourceUris.includes('grados://papers/index') ? '✅' : '❌'} (${resourceUris.length} resources)`);
    console.log(`   Resource template:    ${templateUris.includes('grados://papers/{safe_doi}') ? '✅' : '❌'}`);
    console.log(`   Search works:         ${dois.length > 0 ? '✅' : '❌'} (${dois.length} results)`);
    console.log(`   Extraction contract:  ${!isError && extractSummary?.kind === 'paper_saved_summary' ? '✅' : (isError ? '⚠️  live extraction failed' : '❌')}`);
    console.log(`   MD saved to markdown/: ${mdExists ? '✅' : (isError ? '⚠️  extraction failed' : '❌')}`);
    console.log(`   Read-back works:      ${mdExists ? (readOk ? '✅' : '❌') : '⚠️  skipped'}`);
    console.log(`   Zotero graceful fail: ${zoteroIsError ? '✅' : '❌'}`);

    await client.close();
    process.exit(0);
}

test().catch(e => {
    console.error('Test error:', e);
    process.exit(1);
});
