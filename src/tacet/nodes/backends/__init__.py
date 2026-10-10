"""Device capture backends (receive-only drivers).

Each backend module exposes the contract consumed by the front-end
(tacet.nodes.capture): a SCENARIOS registry, validate(args),
check_available(), capture_once(ns, session_dir, index) -> entry parts,
and an optional scan(args, session_dir). Backends own config assembly,
file writes, sha256 per file, byte-count checks, and the device/format/
gain/channel parts of the manifest entry; session hygiene stays in the
front-end. hackrf and pluto today.
"""
