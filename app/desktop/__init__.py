"""RedactGate Desktop — offline document anonymizer built on the RedactGate engine.

Everything here runs on the user's computer: detection (RedactGate regex packs + local
rule-based NER), the reversible label vault (AES-256-GCM, same crypto as
:mod:`app.redaction.vault`) and the document format handlers. Nothing is sent anywhere —
:mod:`app.desktop.netguard` refuses every non-loopback socket connection in-process.
"""

__version__ = "0.1.0"
