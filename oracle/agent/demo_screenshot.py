"""One-question research run, staged for the blog screenshot (not committed).

  cd oracle/agent && ../../.venv/bin/python demo_screenshot.py
"""
from db import connect
from research_agent import run_research
import anthropic

QUESTION = ("What have I published with the Oracle team about agent memory and RAG, "
            "and what's new from Oracle in that space?")
# formatting directive sent with the question but kept out of the printed frame
STYLE = (" Keep it brief and screenshot-friendly, under 150 words total: "
         "one short bullet per published video (title + guest + one clause), "
         "then a 'What's new' section with 2-3 bullets from current web research on Oracle's "
         "agent-memory and RAG work. PLAIN TEXT ONLY: no markdown, no asterisks, no bold "
         "markers (this prints in a terminal). Read and cite ONLY my published videos, never "
         "open or reference planning/organizing chats, no view counts, no other companies' "
         "products.")


def main():
    client = anthropic.Anthropic()
    conn = connect()
    try:
        print("Q:", QUESTION)
        answer, sources = run_research(client, conn, QUESTION + STYLE)
        print("\nANSWER:\n" + answer)
        print("\nGROUNDED IN YOUR CONTENT:")
        for t in sources:
            print("  -", t)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
