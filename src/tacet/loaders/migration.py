"""v1 -> v2 manifest migration (flat list -> wrapper + v2 fields).

Rules (from the manifest v2 spec):

- Flat list -> wrapper ``{"schema_version": "2.0", "recordings": [...]}``.
- ``protocol: "noise"`` entries: band="2.4 GHz ISM" (band is ALWAYS the
  physical band, never a class label), label="background",
  condition="tx_off", distance_m=null, los=null.
- ``protocol: "elrs"`` entries: label="elrs", condition="bench",
  distance_m=1.0 (operator notes say "bench ~1m"), los=true,
  elrs_profile=null (was not logged; not fabricated).
- Every entry: environment="indoor_bench", contributor="juanmicl",
  source="own_capture", consent="private", site_anonymized=true.
- Ids, paths and checksums are never touched.

The migration is idempotent: documents/entries already at v2 pass through
unchanged, so running it twice produces no differences. Entries are written
back in a fixed key order so output is deterministic. Protocols without a
rule fail loudly instead of being guessed.
"""

_KEY_ORDER = (
    "id", "schema_version", "timestamp", "device", "protocol", "purpose",
    "center_freq_hz", "sample_rate_sps", "format", "duration_s", "channels",
    "antenna", "operator_notes", "band", "label", "condition", "distance_m",
    "environment", "los", "elrs_profile", "tx_power_dbm", "gain", "rf_chain",
    "contributor", "source", "consent", "site_anonymized",
)


def migrate_entry(entry: dict) -> dict:
    """Return the v2 form of one entry; entries already at v2 pass through."""
    e = dict(entry)
    if e.get("schema_version") == "2.0":
        return {k: e[k] for k in _KEY_ORDER if k in e}
    protocol = e.get("protocol")
    if protocol == "noise":
        e["band"] = "2.4 GHz ISM"
        e["label"] = "background"
        e["condition"] = "tx_off"
        e["distance_m"] = None
        e["los"] = None
    elif protocol == "elrs":
        e["label"] = "elrs"
        e["condition"] = "bench"
        e["distance_m"] = 1.0
        e["los"] = True
        e.setdefault("elrs_profile", None)
    else:
        raise ValueError(
            f"migration rule missing for protocol {protocol!r} "
            f"(id {e.get('id')!r}); extend migration.py instead of guessing")
    e["environment"] = "indoor_bench"
    e["contributor"] = "juanmicl"
    e["source"] = "own_capture"
    e["consent"] = "private"
    e["site_anonymized"] = True
    e["schema_version"] = "2.0"
    return {k: e[k] for k in _KEY_ORDER if k in e}


def migrate_manifest(doc):
    """Migrate a manifest document (v1 flat list or v2 wrapper) to v2.

    Idempotent: a document already at v2 is returned unchanged.
    """
    if isinstance(doc, dict) and doc.get("schema_version") == "2.0":
        return doc
    if isinstance(doc, list):
        recordings = doc
    elif isinstance(doc, dict) and isinstance(doc.get("recordings"), list):
        recordings = doc["recordings"]
    else:
        raise ValueError("manifest must be a list or a v2 wrapper object")
    return {
        "schema_version": "2.0",
        "recordings": [migrate_entry(e) for e in recordings],
    }
