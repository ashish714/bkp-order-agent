# BKP Order Agent

An AI customer-support agent for **BKP**, built with Claude. Customers chat with
"Bee" to:

- **Track an order:** status, carrier, tracking number, delivery estimate, full timeline.
- **Get order details:** items, prices, totals, payment method, address.
- **Request a refund or cancellation:** checks eligibility, quotes the amount, and processes it once the customer confirms.
- **Ask questions:** shipping, returns, payments and contact info, answered from `data/faq.md`.

You can chat with it in a **web page** or in the **terminal**.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export ANTHROPIC_API_KEY=sk-ant-...      # from https://console.anthropic.com

# Web chat at http://localhost:8000
uvicorn bkp_agent.web.server:app --port 8000

# ...or chat in the terminal
bkp-agent
```

Run the tests (no API key needed; they use a scripted fake Claude client):

```bash
pytest
```

## Try it with the sample orders

The first run creates `data/bkp.db` from `data/orders.json`. Delete that file
to reset the demo data.

| Order | Email | Status | Try asking |
|---|---|---|---|
| BKP-10001 | priya@example.com | Delivered (within 30 days) | "I want to return my headphones" |
| BKP-10002 | rahul@example.com | In transit | "Where is my order?" |
| BKP-10003 | ananya@example.com | Processing | "Cancel my order please" |
| BKP-10004 | priya@example.com | Delivered (more than 30 days ago) | "Can I get a refund?" (answer: no) |
| BKP-10005 | vikram@example.com | Cancelled | "What happened to my order?" |

A refund conversation goes like this:

1. The customer asks for a refund and gives the order ID and email.
2. Bee checks eligibility, creates a *pending* refund, and tells them the amount, where the money goes, and how long it takes.
3. The customer says yes, and Bee confirms the refund. If they say no, nothing happens.

## How it works

```
customer ──► SupportAgent.chat() ──► Claude (claude-opus-5-5)
                  ▲                        │ tool calls
                  │                        ▼
                  └──── tool results ◄── OrderStore (SQLite)
```

- `src/bkp_agent/agent.py`: the agent. It holds the system prompt (with the FAQ embedded), the tool definitions, and the tool-use loop. Conversation history is kept per chat.
- `src/bkp_agent/store.py`: orders and refunds in SQLite, plus the refund-eligibility rules.
- `src/bkp_agent/web/`: the FastAPI server and a single-page chat UI.
- `src/bkp_agent/cli.py`: the terminal chat.

Tools Claude can call: `get_order_status`, `get_order_details`,
`check_refund_eligibility`, `request_refund`, `confirm_refund`.

### Safety built in

- **Identity check:** every lookup needs the order ID *and* the email it was placed with. A wrong email and an unknown order get the same error, so nobody can probe for valid order IDs.
- **Refunds need a real "yes":** `request_refund` only creates a pending refund. `confirm_refund` is refused in code unless the customer has sent another message since the refund was quoted. Even if the model tried to skip asking, no refund goes through until the customer replies.
- **Rules live in code, not the prompt:** the 30-day window, no refunds while in transit, non-refundable shipping fees, and duplicate-refund protection are enforced in `store.py`.
- **Refusal fallback:** requests use the API's server-side fallback (`fallbacks: "default"`). If Claude declines a request, a fallback model answers instead of the chat dead-ending.

## Making it BKP's real system

The demo data is local. To connect real systems, replace the `OrderStore`
methods with calls to BKP's order-management, shipping and payments APIs,
keeping the same return shapes. Also:

- Replace `data/faq.md` with BKP's real policies.
- Keep chat sessions in Redis or a database instead of memory if you run several server processes.
- Put the web chat behind proper customer login, so the email check isn't the only identity check.
- Send real refunds to the payment gateway in `confirm_refund`.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | (required) | Your Anthropic API key |
| `BKP_DB_PATH` | `data/bkp.db` | SQLite database location |

The model and effort level are set at the top of `src/bkp_agent/agent.py`.
