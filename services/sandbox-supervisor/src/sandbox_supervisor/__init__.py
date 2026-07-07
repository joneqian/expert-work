"""Expert Work Sandbox Supervisor — gVisor sandbox lifecycle service (Stream F.1).

Internal HTTP service the control-plane calls to ``acquire`` / ``release`` /
``destroy`` ``exec_python`` sandbox containers. See STREAM-F-DESIGN § 2.1.
"""
