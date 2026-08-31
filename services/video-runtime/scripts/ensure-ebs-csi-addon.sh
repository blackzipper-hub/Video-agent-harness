#!/usr/bin/env bash
# Idempotent: ensure EKS managed add-on aws-ebs-csi-driver + IRSA role.
# Does not modify application namespaces, drain nodes, or restart unrelated workloads.
set -euo pipefail

CLUSTER_NAME="${EKS_CLUSTER_NAME:?set EKS_CLUSTER_NAME}"
REGION="${AWS_REGION:?set AWS_REGION}"
ROLE_NAME="${EBS_CSI_ROLE_NAME:-${CLUSTER_NAME}-ebs-csi-controller}"
POLICY_ARN="arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"

ISSUER="$(aws eks describe-cluster --name "${CLUSTER_NAME}" --region "${REGION}" \
  --query 'cluster.identity.oidc.issuer' --output text)"
if [[ -z "${ISSUER}" || "${ISSUER}" == "None" ]]; then
  echo "ERROR: cluster has no OIDC issuer; enable IRSA / use a supported EKS version." >&2
  exit 1
fi

OIDC_HOSTPATH="${ISSUER#https://}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
OIDC_ARN="arn:aws:iam::${ACCOUNT}:oidc-provider/${OIDC_HOSTPATH}"
ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${ROLE_NAME}"

ensure_oidc_provider() {
  if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "${OIDC_ARN}" &>/dev/null; then
    echo "OIDC provider already exists: ${OIDC_ARN}"
    return 0
  fi
  echo "Creating IAM OIDC provider for ${ISSUER} ..."
  local thumbprint
  thumbprint="$(echo | openssl s_client -servername "${OIDC_HOSTPATH%%/*}" -connect "${OIDC_HOSTPATH}:443" 2>/dev/null \
    | openssl x509 -fingerprint -noout -sha1 2>/dev/null | cut -d= -f2 | tr -d ':')"
  if [[ -z "${thumbprint}" ]]; then
    echo "ERROR: could not compute OIDC thumbprint (openssl)." >&2
    exit 1
  fi
  aws iam create-open-id-connect-provider \
    --url "${ISSUER}" \
    --client-id-list sts.amazonaws.com \
    --thumbprint-list "${thumbprint}"
}

ensure_iam_role() {
  local trust_file
  trust_file="$(mktemp)"
  trap 'rm -f "${trust_file}"' RETURN
  cat >"${trust_file}" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "${OIDC_ARN}"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "${OIDC_HOSTPATH}:aud": "sts.amazonaws.com",
          "${OIDC_HOSTPATH}:sub": "system:serviceaccount:kube-system:ebs-csi-controller-sa"
        }
      }
    }
  ]
}
EOF
  if aws iam get-role --role-name "${ROLE_NAME}" &>/dev/null; then
    echo "IAM role already exists: ${ROLE_NAME}"
  else
    echo "Creating IAM role ${ROLE_NAME} ..."
    aws iam create-role \
      --role-name "${ROLE_NAME}" \
      --assume-role-policy-document "file://${trust_file}" \
      --description "IRSA for EKS EBS CSI driver (${CLUSTER_NAME})"
  fi
  local attached
  attached="$(aws iam list-attached-role-policies --role-name "${ROLE_NAME}" \
    --query "AttachedPolicies[?PolicyArn=='${POLICY_ARN}'].PolicyArn | [0]" --output text)"
  if [[ "${attached}" != "${POLICY_ARN}" ]]; then
    echo "Attaching ${POLICY_ARN} ..."
    aws iam attach-role-policy --role-name "${ROLE_NAME}" --policy-arn "${POLICY_ARN}"
  else
    echo "Policy already attached to ${ROLE_NAME}"
  fi
}

addon_status() {
  aws eks describe-addon --cluster-name "${CLUSTER_NAME}" --region "${REGION}" \
    --addon-name aws-ebs-csi-driver --query 'addon.status' --output text 2>/dev/null || true
}

addon_role_arn() {
  aws eks describe-addon --cluster-name "${CLUSTER_NAME}" --region "${REGION}" \
    --addon-name aws-ebs-csi-driver --query 'addon.serviceAccountRoleArn' --output text 2>/dev/null || true
}

ensure_addon() {
  local status role
  status="$(addon_status)"
  if [[ -z "${status}" || "${status}" == "None" ]]; then
    echo "Creating EKS add-on aws-ebs-csi-driver ..."
    aws eks create-addon \
      --cluster-name "${CLUSTER_NAME}" \
      --region "${REGION}" \
      --addon-name aws-ebs-csi-driver \
      --service-account-role-arn "${ROLE_ARN}"
    echo "Waiting for add-on to become ACTIVE ..."
    aws eks wait addon-active --cluster-name "${CLUSTER_NAME}" --region "${REGION}" --addon-name aws-ebs-csi-driver
    return 0
  fi

  echo "Add-on status: ${status}"
  if [[ "${status}" == "ACTIVE" ]]; then
    role="$(addon_role_arn)"
    if [[ -n "${role}" && "${role}" != "None" && "${role}" != "${ROLE_ARN}" ]]; then
      echo "NOTE: add-on uses role ${role} (not ${ROLE_ARN}). Leaving unchanged; if PVCs fail, align IAM manually."
    fi
    if [[ -z "${role}" || "${role}" == "None" ]]; then
      echo "Updating add-on to attach service account role ${ROLE_ARN} ..."
      aws eks update-addon \
        --cluster-name "${CLUSTER_NAME}" \
        --region "${REGION}" \
        --addon-name aws-ebs-csi-driver \
        --service-account-role-arn "${ROLE_ARN}"
      aws eks wait addon-active --cluster-name "${CLUSTER_NAME}" --region "${REGION}" --addon-name aws-ebs-csi-driver
    fi
    return 0
  fi

  if [[ "${status}" == "CREATING" || "${status}" == "UPDATING" ]]; then
    echo "Waiting for add-on (current ${status}) ..."
    aws eks wait addon-active --cluster-name "${CLUSTER_NAME}" --region "${REGION}" --addon-name aws-ebs-csi-driver
    return 0
  fi

  echo "ERROR: add-on is ${status}. Fix in AWS Console or: aws eks describe-addon --cluster-name ${CLUSTER_NAME} --addon-name aws-ebs-csi-driver" >&2
  exit 1
}

echo "=== EBS CSI (managed add-on) — cluster=${CLUSTER_NAME} region=${REGION} ==="
ensure_oidc_provider
ensure_iam_role
ensure_addon
echo "Done. Verify: kubectl get csidriver ebs.csi.aws.com && kubectl get pods -n kube-system -l app.kubernetes.io/name=aws-ebs-csi-driver"
