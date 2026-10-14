"""Chat with the BKP support agent in the terminal."""
import anthropic

from bkp_agent.agent import NO_CREDENTIALS_MSG, is_missing_credentials
from bkp_agent.config import ai_enabled, make_agent, open_store


def main() -> None:
    agent = make_agent(open_store())
    mode = "AI mode (Claude)" if ai_enabled() else "simple mode, no API key needed"
    print(f"[{mode}]")
    print("Bee (BKP support): Hi! I can track orders, show order details, help with "
          "refunds, or answer questions. Type 'quit' to exit, 'reset' to start over.\n")
    while True:
        try:
            msg = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not msg:
            continue
        if msg.lower() in ("quit", "exit"):
            return
        if msg.lower() == "reset":
            agent.reset()
            print("Bee: Starting fresh. How can I help?\n")
            continue
        try:
            reply = agent.chat(msg)
        except anthropic.AuthenticationError:
            print("Error: your Anthropic API key was rejected. Check ANTHROPIC_API_KEY.\n")
            return
        except TypeError as e:
            if not is_missing_credentials(e):
                raise
            print(f"Error: {NO_CREDENTIALS_MSG}\n")
            return
        except anthropic.APIConnectionError:
            print("Error: couldn't reach the Claude API. Check your connection.\n")
            continue
        except anthropic.APIStatusError as e:
            print(f"Error from the Claude API ({e.status_code}): {e.message}\n")
            continue
        print(f"Bee: {reply}\n")


if __name__ == "__main__":
    main()
