# Copyright (c) 2026 Microsoft Corporation.
# Licensed under the MIT License

"""CLI for independent, repeatable entity-resolution comparisons."""

import argparse
import asyncio
import sys
from pathlib import Path

from bank_project.resolution.engine.contracts import (
    Corpus,
    ResolverConfig,
    read_json,
    require,
)
from bank_project.resolution.engine.evaluation import annotation_template
from bank_project.resolution.engine.runner import compare_methods, write_json


def main() -> None:
    """Create annotation tasks or run a paired offline/LLM experiment."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    annotate = commands.add_parser("annotate", help="Export an unreviewed gold template")
    annotate.add_argument("--corpus", type=Path, required=True)
    annotate.add_argument("--output", type=Path, required=True)
    compare = commands.add_parser(
        "compare", help="Run frozen legacy and evidence methods on the same records"
    )
    compare.add_argument("--corpus", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True, help="A NEW experiment directory")
    compare.add_argument("--gold", type=Path)
    compare.add_argument(
        "--method", choices=["evidence_v1", "synonym_llm_v1"], default="evidence_v1"
    )
    compare.add_argument(
        "--synonyms",
        type=Path,
        help="Versioned, reviewed synonym dictionary for candidate retrieval",
    )
    compare.add_argument("--max-alias-calls", type=int, default=200)
    compare.add_argument("--vectors", type=Path, help="Optional versioned embedding sidecar")
    compare.add_argument("--config", type=Path, help="ResolverConfig JSON; no credentials")
    compare.add_argument(
        "--root",
        type=Path,
        help="Existing GraphRAG settings directory, enables LLM judging",
    )
    compare.add_argument("--model-id", default="default_chat_model")
    compare.add_argument(
        "--legacy-model-config",
        action="store_true",
        help="Read an existing GraphRAG 2.5 openai_chat model configuration",
    )
    compare.add_argument(
        "--model-revision",
        help="Auditable deployment/model revision; required with --root",
    )
    compare.add_argument("--cache", type=Path, help="Optional reusable local judge cache")
    args = parser.parse_args()
    # The existing GraphRAG config loader changes cwd to the model project.
    # Bind experiment paths to the caller's cwd before loading that project.
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    judge = None
    try:
        corpus = Corpus.from_dict(read_json(args.corpus))
        require(
            not args.output.exists(),
            "Output already exists; choose a new path to preserve previous results",
        )
        if args.command == "annotate":
            write_json(args.output, annotation_template(corpus))
        else:
            config = (
                ResolverConfig(**read_json(args.config))
                if args.config
                else ResolverConfig(
                    model_policy="apply" if args.method == "synonym_llm_v1" else "review"
                )
            )
            gold = read_json(args.gold) if args.gold else None
            require(
                args.method != "synonym_llm_v1" or args.root is not None,
                "--root is required for synonym_llm_v1; no offline rule fallback",
            )
            if args.root:
                require(
                    bool(args.model_revision),
                    "--model-revision is required with --root",
                )
                from bank_project.resolution.engine.model_judge import from_project

                # Keep the cache outside the exclusive result directory.
                cache = args.cache or args.output.parent / ".entity-resolution-judge.sqlite"
                judge = from_project(
                    args.root,
                    args.model_id,
                    cache,
                    corpus.namespace,
                    args.model_revision,
                    **({"legacy_config": True} if args.legacy_model_config else {}),
                )
            report = asyncio.run(
                compare_methods(
                    corpus,
                    args.output,
                    config=config,
                    gold=gold,
                    judge=judge,
                    vectors=read_json(args.vectors) if args.vectors else None,
                    method=args.method,
                    synonyms=read_json(args.synonyms) if args.synonyms else None,
                    max_alias_calls=args.max_alias_calls,
                )
            )
            sys.stdout.write(
                f"{report['evaluation_status']}\n{args.output.resolve() / 'comparison.md'}\n"
            )
    except (ValueError, TypeError, OSError) as exc:
        parser.exit(2, f"Entity resolution: {exc}\n")
    finally:
        if judge is not None:
            judge.close()


if __name__ == "__main__":
    main()
