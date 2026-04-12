## Certificate Expiry Runbook

### Immediate Identification
1. Find expiring/expired certs: `kubectl get secrets -A | grep tls`
2. Check cert expiry: `echo | openssl s_client -connect payment-processor.internal:443 2>/dev/null | openssl x509 -noout -dates`
3. Check Vault cert: `vault pki list-intermediate`

### Remediation Steps
1. Renew cert via cert-manager: `kubectl annotate cert <cert-name> cert-manager.io/issue-temporary-certificate=true`
2. Manual renewal if cert-manager unavailable: Contact PKI team with CSR
3. Restart affected services after cert rotation to pick up new cert

### Prevention
- Set up monitoring for cert expiry < 30 days
- cert-manager auto-renewal should be configured with 80% lifetime trigger