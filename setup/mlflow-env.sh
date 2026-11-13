# Source this file: source setup/mlflow-env.sh
# Requires an active `oc login` session. The token stays in your shell environment.

if ! MLFLOW_TRACKING_TOKEN="$(oc whoami -t)"; then
  return 1 2>/dev/null || exit 1
fi

if ! MLFLOW_TRACKING_URI="$(oc get mlflows.mlflow.opendatahub.io mlflow -o jsonpath='{.status.url}')"; then
  return 1 2>/dev/null || exit 1
fi
if [ -z "$MLFLOW_TRACKING_URI" ]; then
  echo 'MLflow resource mlflow has no status.url yet.' >&2
  return 1 2>/dev/null || exit 1
fi

export MLFLOW_TRACKING_URI
export MLFLOW_TRACKING_AUTH=kubernetes
export MLFLOW_TRACKING_TOKEN
export MLFLOW_WORKSPACE='mlflow-demo'

# Local comparison evaluations rerun the agent against Qwen.
if ! LLM_BASE_URL="$(oc get llminferenceservice qwen3-14b -n llm-inference -o jsonpath='{.status.url}')"; then
  return 1 2>/dev/null || exit 1
fi
if [ -z "$LLM_BASE_URL" ]; then
  echo 'LLMInferenceService llm-inference/qwen3-14b has no status.url yet.' >&2
  return 1 2>/dev/null || exit 1
fi
LLM_BASE_URL="${LLM_BASE_URL%/}"
case "$LLM_BASE_URL" in
  */v1) ;;
  *) LLM_BASE_URL="$LLM_BASE_URL/v1" ;;
esac
export LLM_BASE_URL
export LLM_API_KEY="$MLFLOW_TRACKING_TOKEN"
# An empty exported value also overrides a pod-only token path in .env.
export LLM_API_KEY_FILE=''
