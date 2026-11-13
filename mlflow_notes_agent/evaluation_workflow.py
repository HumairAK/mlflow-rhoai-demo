"""Evaluate a candidate prompt, optionally alongside a baseline version."""

from __future__ import annotations

import argparse

import mlflow

from mlflow_notes_agent.agent import build_agent
from mlflow_notes_agent.config import get_settings
from mlflow_notes_agent.judges import get_scorers
from mlflow_notes_agent.prompt_workflow import PROMPT_NAME


def evaluate_prompt(dataset, prompt_version: int, judge_model_uri: str | None = None):
    prompt_uri = f"prompts:/{PROMPT_NAME}/{prompt_version}"
    agent = build_agent(prompt_uri)

    # Traces exported from this agent have an `inputs.messages` field.
    def predict_fn(messages):
        return agent.invoke({"messages": messages})

    result = mlflow.genai.evaluate(
        data=dataset,
        predict_fn=predict_fn,
        scorers=get_scorers(judge_model_uri),
    )

    client = mlflow.MlflowClient()
    client.set_tag(result.run_id, "mlflow.runName", f"prompt-v{prompt_version}-evaluation")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a mortgage prompt, optionally comparing it with a baseline.")
    parser.add_argument("--dataset-name", required=True)
    parser.add_argument("--baseline-version", type=int, help="Also evaluate this baseline version.")
    parser.add_argument("--candidate-version", type=int, required=True)
    parser.add_argument("--judge-model-uri")
    args = parser.parse_args()

    mlflow.set_experiment(get_settings().mlflow_experiment)
    dataset = mlflow.genai.datasets.get_dataset(name=args.dataset_name)

    if args.baseline_version is not None:
        baseline = evaluate_prompt(dataset, args.baseline_version, args.judge_model_uri)
        print(f"Baseline run:  {baseline.run_id} {baseline.metrics}")

    candidate = evaluate_prompt(dataset, args.candidate_version, args.judge_model_uri)
    print(f"Candidate run: {candidate.run_id} {candidate.metrics}")
