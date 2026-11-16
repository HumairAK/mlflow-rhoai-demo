# Demo architecture

The workspace is scoped to the project, while the MLflow service is shared.
The workspace box represents logical ownership, not physical storage in the
namespace. The shared MLflow server uses PostgreSQL and MinIO for persistence;
those storage services are omitted from this slide.

```mermaid
flowchart LR
    Browser["Browser"]
    CLI["Judging CLI"]
    OpenAI["OpenAI API<br/>Evaluation judge"]

    subgraph Demo["mlflow-demo namespace"]
        Agent["Mortgage Agent"]
        WB["Workbench"]
        Workspace["MLflow workspace: mlflow-demo<br/>Experiments · Traces · Evals · Prompts"]
    end

    subgraph Inference["llm-inference namespace"]
        Qwen["KServe → llm-d → vLLM<br/>Qwen"]
    end

    subgraph Platform["redhat-ods-applications namespace"]
        MLflow["Shared MLflow Server"]
    end

    Browser -->|"Chat"| Agent
    Browser -->|"View results / Manage prompts"| Workspace
    Agent -->|"Inference"| Qwen
    Agent -->|"Load prompt / Log traces"| Workspace
    WB -->|"Evaluate"| Workspace
    CLI -->|"Read traces / Save scores"| Workspace
    CLI -->|"Judge responses"| OpenAI
    Workspace -.->|"Served by"| MLflow
```

The agent loads its prompt from the workspace's MLflow Prompt Registry using
`mortgage-lending-agent@production`. Moving the alias selects the prompt version
used by the deployed agent after its short cache expires.
