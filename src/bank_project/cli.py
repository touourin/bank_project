import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Bank project framework")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        "bank_project.main:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
