#!/usr/bin/env bash
# 本机打开 monitoring 命名空间里的 Grafana：打印 admin 密码并 port-forward。
# 用法：在仓库根目录 ./scripts/port-forward-grafana.sh
# aws eks update-kubeconfig（可选）：合并进现有 kubeconfig，不会删掉别的集群；但会把「当前上下文」切到该集群。
# 默认不执行，避免覆盖你已选好的 current-context；新电脑或凭证过期时再：UPDATE_KUBECONFIG=1 ./scripts/port-forward-grafana.sh
# 环境变量：EKS_CLUSTER_NAME、AWS_REGION、GRAFANA_NS（默认 monitoring）、GRAFANA_LOCAL_PORT（默认 3130）、UPDATE_KUBECONFIG=1 才刷新 kubeconfig
set -euo pipefail

EKS_CLUSTER_NAME="${EKS_CLUSTER_NAME:-cuti-cluster}"
AWS_REGION="${AWS_REGION:-ap-southeast-2}"
GRAFANA_NS="${GRAFANA_NS:-monitoring}"
GRAFANA_LOCAL_PORT="${GRAFANA_LOCAL_PORT:-3130}"

if [[ "${UPDATE_KUBECONFIG:-0}" == "1" ]]; then
  aws eks update-kubeconfig --name "${EKS_CLUSTER_NAME}" --region "${AWS_REGION}"
fi

if ! kubectl get secret grafana-admin-credentials -n "${GRAFANA_NS}" &>/dev/null; then
  echo "ERROR: secret grafana-admin-credentials not found in namespace ${GRAFANA_NS}" >&2
  exit 1
fi

if ! kubectl get svc grafana -n "${GRAFANA_NS}" &>/dev/null; then
  echo "ERROR: Service grafana not found in ${GRAFANA_NS}. Deploy Grafana first (e.g. .github/workflows/deploy-logging.yml Grafana step, or helm upgrade --install grafana per helm/logging/grafana-values.yaml)." >&2
  exit 1
fi

echo "Grafana URL:  http://127.0.0.1:${GRAFANA_LOCAL_PORT}"
echo "Grafana user: admin"
echo -n "Grafana pass: "
kubectl get secret grafana-admin-credentials -n "${GRAFANA_NS}" -o jsonpath='{.data.admin-password}' | base64 -d
echo
echo "Port-forward (Ctrl+C to stop)..."
exec kubectl port-forward -n "${GRAFANA_NS}" "svc/grafana" "${GRAFANA_LOCAL_PORT}:80"
