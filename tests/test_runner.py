from __future__ import annotations

from ai_router_control.runner import parse_vllm_metrics


def test_parse_vllm_metrics_collects_relevant_values():
    metrics = """
    # HELP vllm:prompt_tokens_total Number of prompt tokens
    vllm:prompt_tokens_total{model_name="qwen",engine="0"} 12
    vllm:prompt_tokens_total{model_name="qwen",engine="1"} 8
    vllm:generation_tokens_total{model_name="qwen"} 5
    vllm:num_requests_running{model_name="qwen"} 2
    vllm:num_requests_waiting{model_name="qwen"} 1
    vllm:kv_cache_usage_perc{model_name="qwen"} 0.25
    unrelated_metric 99
    """

    assert parse_vllm_metrics(metrics) == {
        "prompt_tokens_total": 20.0,
        "generation_tokens_total": 5.0,
        "requests_running": 2.0,
        "requests_waiting": 1.0,
        "kv_cache_usage": 0.25,
    }
