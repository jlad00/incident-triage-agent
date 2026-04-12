## ml-inference OOM Runbook

### Immediate Actions
1. Check current memory usage: `kubectl top pod -l app=ml-inference`
2. Check restart count: `kubectl get pods -l app=ml-inference`
3. Review recent model load: was a new/larger model loaded recently?

### Common Causes
- Model batch size too large for container memory limit
- Memory leak in preprocessing pipeline
- Container limit undersized for current workload

### Remediation Options
- Scale horizontally: `kubectl scale deployment ml-inference --replicas=3`
- Increase memory limit in deployment spec (requires re-deploy)
- Reduce batch size via env var: `MODEL_BATCH_SIZE=16`
- Restart pod to clear leak (temporary): `kubectl rollout restart deployment/ml-inference`