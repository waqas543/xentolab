
import argparse
import time
import nltk

from sumy.parsers.plaintext import PlaintextParser
from sumy.nlp.tokenizers import Tokenizer
from sumy.summarizers.lex_rank import LexRankSummarizer


def summarize_text(text: str, sentence_count: int = 5) -> dict:
    if not text or not text.strip():
        raise ValueError("Input text cannot be empty.")

    start_time = time.perf_counter()

    parser = PlaintextParser.from_string(
        text,
        Tokenizer("english")
    )

    summarizer = LexRankSummarizer()

    sentences = summarizer(
        parser.document,
        sentence_count
    )

    summary = " ".join(str(sentence) for sentence in sentences)

    execution_time = time.perf_counter() - start_time

    return {
        "summary": summary,
        "original_characters": len(text),
        "summary_characters": len(summary),
        "original_words": len(text.split()),
        "summary_words": len(summary.split()),
        "execution_time": round(execution_time, 4),
    }


def main():
    nltk.download("punkt_tab", quiet=True)

    cli = argparse.ArgumentParser(
        description="Text summarization using Sumy LexRank"
    )

    cli.add_argument(
        "--sentences",
        type=int,
        default=5,
        help="Number of sentences in summary"
    )

    cli.add_argument(
        "--input",
        type=str,
        help="Path to input text file"
    )

    args = cli.parse_args()

    if args.sentences < 1:
        cli.error("--sentences must be greater than zero")

    if args.input:
        with open(args.input, "r", encoding="utf-8") as file:
            text = file.read()
    else:
        print("Paste your text below.")
        print("Enter END on a separate line when finished.\n")

        lines = []

        while True:
            try:
                line = input()
            except EOFError:
                break

            if line.strip() == "END":
                break

            lines.append(line)

        text = "\n".join(lines)

    try:
        result = summarize_text(
            text,
            sentence_count=args.sentences
        )

        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)

        print(result["summary"])

        print("\n" + "=" * 60)
        print("STATISTICS")
        print("=" * 60)

        for key, value in result.items():
            if key != "summary":
                print(f"{key}: {value}")

    except ValueError as error:
        print(f"Error: {error}")


if __name__ == "__main__":
    main()
