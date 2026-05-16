"""Helix-Agent Credential Proxy — outbound secret injection (Stream F.5).

A sandbox's outbound HTTP goes through this service: it resolves an
``X-Helix-Secret-Ref`` to a real credential and injects it before
forwarding upstream, so the secret value never enters the sandbox.
See STREAM-F-DESIGN § 2.5.
"""
