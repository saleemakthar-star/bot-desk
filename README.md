# Bot Desk

Public trading blotter for the Robinhood **agentic** desk. The dashboard is a frozen snapshot of cash, the leftover Walmart stub, closed AMD/QCOM call scalps, and the order tape.

Live site: [https://saleemakthar-star.github.io/bot-desk/](https://saleemakthar-star.github.io/bot-desk/)

## Local

```bash
python3 server.py
```

Then open [http://127.0.0.1:43147](http://127.0.0.1:43147).

## GitHub Pages

Pushes to `main` deploy through `.github/workflows/pages.yml`. The workflow authenticates with the built-in `GITHUB_TOKEN` secret, enables Pages (`build_type: workflow`), and publishes `index.html`.
