"""Console entry point: `tacet <subcommand>`."""


def app(argv=None) -> int:
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print("usage: tacet <command> [args]\ncommands: capture")
        return 0
    if argv[0] == "capture":
        from tacet.nodes.capture import main as capture_main

        return capture_main(argv[1:])
    print(f"unknown command: {argv[0]}", file=sys.stderr)
    return 2


def main() -> None:
    raise SystemExit(app())
