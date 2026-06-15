"""Demo: ask the research agent questions about YOUR content.

Needs ANTHROPIC_API_KEY (in oracle/.env). The DB + content are already loaded.

  cd oracle/agent
  ../../.venv/bin/python demo_research.py
"""
from db import connect
from research_agent import run_research
import anthropic

QUESTIONS = [
    "What have I made about using AI in my actual workflow?",
    "If someone wants to break into tech, what advice have I shared?",
    # needs BOTH my content (LLM inference video) AND the web (current 2026 info):
    "I made a video on LLM inference vs traditional inference — what are the latest "
    "2026 developments in LLM inference I could make a follow-up about?",
]


def main():
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    conn = connect()
    try:
        for q in QUESTIONS:
            print("\n" + "=" * 70)
            print("Q:", q)
            answer, sources = run_research(client, conn, q)
            print("\nANSWER:\n" + answer)
            print("\nGROUNDED IN YOUR CONTENT:")
            for t in sources:
                print("  -", t)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
