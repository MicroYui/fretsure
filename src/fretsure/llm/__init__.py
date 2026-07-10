"""LLM access behind a small protocol seam.

``LLMClient`` is the dependency-injection point: all agent logic is tested
deterministically with ``FakeLLM``; ``ProxyLLM`` talks to the real (local) proxy
only in ``@pytest.mark.integration`` tests. Model ids must NOT carry the ``[1m]``
suffix — the proxy rejects those.
"""
