import asyncio
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from agent import Agent, PromptAgent, FINAL_ANSWER
from client import GroqClient, BedRockClient, ClientConfig
from tool import code_executor
from memory import ChatBufferMemory

from retrieve_agent import advisor
from kube_engineer import engineer

from dotenv import load_dotenv

load_dotenv()


bedrock_client = BedRockClient(
    ClientConfig(
        model="us.meta.llama3-2-90b-instruct-v1:0",
        price_1k_token_in=0.002,  # $0.002 per 1000 input tokens
        price_1k_token_out=0.002,
        ext={"inference_config": {"maxTokens": 2000, "temperature": 0.2}},
    )
)

groq_client = GroqClient(
    ClientConfig(
        model="llama-3.2-90b-vision-preview",
        # model="llama-3.1-70b-versatile",
        # model="llama3-70b-8192",
        temperature=0.2,
        api_key=os.getenv("GROQ_API_KEY"),
    )
)

# llama-3.2-90b-text-preview
groq_client = GroqClient(
    ClientConfig(
        model="llama-3.1-70b-versatile",
        temperature=0.2,
        api_key=os.getenv("GROQ_API_KEY"),
    )
)


def transfer_to_engineer(message: str):
    """Transfers commands or tasks that require direct interaction with Kubernetes clusters via kubectl.
    Ensures that the engineer receives all necessary context to complete each task effectively.
    """
    return engineer


def transfer_to_advisor(message: str):
    """This tool let the planner to obtain troubleshooting guidelines for an issue from the advisor.
    It should invoke once for a specific issue or task!"""
    return advisor


planner = PromptAgent(
    name="Planner",
    client=bedrock_client,
    tools=[transfer_to_advisor, transfer_to_engineer],
    max_iter=20,
    memory=ChatBufferMemory(size=30),
    system=f"""
You are a troubleshoot Planner for Kubernetes Multi-Cluster Environments(Red Hat Advanced Cluster Management (ACM)

## Objective:

Develop a clear, actionable plan to address issues or tasks in Kubernetes multi-cluster environments managed by Red Hat Advanced Cluster Management (RHACM). Use insights from the advisor to help engineers resolve issues efficiently and effectively.

## Troubleshooting Workflow

### 1. Consult the Advisor:

- Start by consulting the Advisor for troubleshooting guidelines related to the identified issue. The Advisor will provide essential insights and recommended steps tailored to the situation.

- You should only consult the Advisor once for a specific issue or task!

### 2. Draft the Action Plan Based on Advisor Guidance:

- Using the Advisor's guidance, draft a clear action plan outlining potential solutions for the issue.

- Break down each solution into executable steps, specifying the `kubectl` commands needed to interact with the Kubernetes clusters.

### 3. Organize the Steps (Sub-Tasks) for the Engineer:

- Instead of sending individual steps(kubectl command), combine the **related steps into one sub-task** for the engineer. This reduces back-and-forth and enhances efficiency.

- Each sub-task for the engineer should try to equipped with the information: **context**, **intent** and **description**! e.g., "Check the `klusterlet-agent` deployment status for any issues using `kubectl describe ...` or `kubectl get ... -oyaml`"

### 4. Verify After Each Sub-Task Completion:

- **If resolved**: Summarize the workflow and present the outcome.
- **If unresolved**: Review progress, update the checklist as needed, and continue with the next steps.
- **If a new issue arises**: Add potential troubleshooting steps or strategies.

## Access Clusters: Use the following method to specify cluster to access in the plan

You can interact with all clusters (hub and managed) using the `KUBECONFIG` environment variable by switching contexts to access different clusters.

Each of these clusters is created using KinD. To access the hub cluster, use the `kind-hub` context.

For managed clusters, switch to the corresponding context in the format `kind-<ManagedCluster>`. For example, to retrieve all pods on `cluster1`, use the following command:

```bash
kubectl get pods -A --context kind-cluster1
```

**You should alway specify which context the to access the cluster when give the task to engineer!!!**

## Knowledge of the Multi Cluster

Note: This section helps you understand the background when drafting the plan.

1. The cluster that manages other clusters is referred to as the hub cluster. It includes the following customized resources and controllers:

  - `ClusterManager` (Global Resource): This resource configures the hub and is reconciled by the `cluster-manager` controller in the `open-cluster-management` namespace by default. The `cluster-manager` watches the `ClusterManager` resource and installs other components in the `open-cluster-management-hub` namespace, including the `addon-manager`, `placement`, `registration`, and `work` controllers.
  
  - `registration`: This component is responsible for registering managed clusters with the hub. It consists of the `cluster-manager-registration-controller` and the `cluster-manager-registration-webhook`, which watches the `CSR` and `ManagedCluster` resources for the managed cluster.
  
  - `addon-manager`: The `cluster-manager-addon-manager-controller` watches the global `ClusterManagementAddon` resource and the namespaced `ManagedClusterAddon` resource. The `ClusterManagementAddon` represents an addon application for the multi-cluster system. The `ManagedClusterAddon` is associated with a specific managed cluster and exists in the cluster namespace, indicating that the addon has been scheduled to that cluster. A single `ClusterManagementAddon` can correspond to multiple `ManagedClusterAddon` instances.
  
  - Placement: This component schedules workloads to target managed clusters. The `cluster-manager-placement-controller` monitors the namespaced `Placement` resource and produces scheduling decisions in the `PlacementDecision` resource.
  
  - Work: The `cluster-manager-work-webhook` controller/webhook manages the `ManifestWork` resource, which encapsulates Kubernetes resources.
  
2. The clusters managed by the hub are represented by the custom resource `ManagedCluster` (abbreviated as mcl and global in scope) within the hub cluster. You can list the clusters currently managed by the hub using `kubectl get mcl` in the hub cluster. The following components are present in the managed cluster:

  - Klusterlet: The `klusterlet` controller in the `open-cluster-management` namespace monitors the global `Klusterlet` resource and installs other controllers such as the `klusterlet-registration-agent` and `klusterlet-work-agent`.
  
  - `klusterlet-registration-agent`: Located in the managed cluster, this agent creates the `CSR` in the hub cluster and monitors/updates the heartbeat(lease) of the `ManagedCluster` in the hub cluster.
  
  - `klusterlet-work-agent`: Also located in the managed cluster, this agent monitors the `ManifestWork` of its namespace in the hub cluster and applies it to the local cluster (the managed cluster). It also updates the `ManifestWork` status in the hub cluster.

**Instructions**

- Once you determine the issue is not exist by the Engineer, Just summarize the result and return. Don't need to consult the Advisor again! 
""",
)


if __name__ == "__main__":
    prompt = sys.argv[1]
    asyncio.run(planner.run(prompt))
