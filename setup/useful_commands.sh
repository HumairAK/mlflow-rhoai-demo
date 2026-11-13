curl -q --fail-with-body --max-time 180 \
  "$LLM_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $(oc whoami -t)" \
  -H 'Content-Type: application/json' \
  -w '\nHTTP %{http_code}\n' \
  -d '{
    "model": "RedHatAI/Qwen3-14B-quantized.w4a16",
    "messages": [{"role": "user", "content": "Reply with a short greeting."}],
    "max_tokens": 128,
    "chat_template_kwargs": {"enable_thinking": false}
  }'

# Without auth to produce 401
curl -q --fail-with-body --max-time 180 \
  "$LLM_BASE_URL/chat/completions" \
  -H 'Content-Type: application/json' \
  -w '\nHTTP %{http_code}\n' \
  -d '{
    "model": "RedHatAI/Qwen3-14B-quantized.w4a16",
    "messages": [{"role": "user", "content": "Reply with a short greeting."}],
    "max_tokens": 128,
    "chat_template_kwargs": {"enable_thinking": false}
  }'