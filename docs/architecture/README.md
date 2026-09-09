# Architecture diagrams

The [architecture document](../ARCHITECTURE.md) is the canonical source. It embeds
the diagrams as Mermaid so they render on GitHub. The standalone SVG files
can be opened in a browser or included in a presentation without a Mermaid renderer.

| Diagram | SVG | Mermaid source |
|---|---|---|
| Components and deployment | [deployment.svg](deployment.svg) | [deployment.mmd](deployment.mmd) |
| Submission, execution and crash recovery | [execution-recovery.svg](execution-recovery.svg) | [execution-recovery.mmd](execution-recovery.mmd) |
| Data model and ownership | [data-ownership.svg](data-ownership.svg) | [data-ownership.mmd](data-ownership.mmd) |
| Automatic package search | [package-search.svg](package-search.svg) | [package-search.mmd](package-search.mmd) |

Edit the named Mermaid blocks in `docs/ARCHITECTURE.md`, then regenerate the
standalone sources and exports with [render.py](render.py). It requires Python,
Node.js and Mermaid CLI 11.12.0 in a separate tooling directory. The service does
not depend on these tools. From the repository root:

```sh
python docs/architecture/render.py --cli /path/to/tooling/node_modules/@mermaid-js/mermaid-cli/src/cli.js
```

When using an already installed Chrome/Chromium browser, pass
`--puppeteer-config /path/to/local-browser.json`. That local file supplies
`executablePath` for the browser; it need not be committed. Styling is in
[mermaid-config.json](mermaid-config.json). SVGs are generated artifacts; update
the Markdown source rather than editing the SVGs or `.mmd` copies directly.
