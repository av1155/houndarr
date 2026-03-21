---
sidebar_position: 3
title: Kubernetes
description: How to deploy Houndarr on Kubernetes using a StatefulSet.
---

# Kubernetes

Houndarr can run on Kubernetes using a StatefulSet with persistent storage.

:::warning
Houndarr uses SQLite. Only one replica is supported — do not scale beyond 1.
:::

## Manifests

Apply all resources with `kubectl apply -f houndarr.yaml`. The sections below
can be combined into a single file separated by `---`.

### Namespace

Optional but keeps things tidy:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: houndarr
```

### StatefulSet

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: houndarr
  namespace: houndarr
spec:
  serviceName: "houndarr"
  replicas: 1 # SQLite — do not increase
  selector:
    matchLabels:
      app: houndarr
  template:
    metadata:
      labels:
        app: houndarr
    spec:
      containers:
        - name: houndarr
          image: ghcr.io/av1155/houndarr:latest
          ports:
            - containerPort: 8877
              name: http
          env:
            - name: TZ
              value: "America/New_York"
            - name: PUID
              value: "1000"
            - name: PGID
              value: "1000"
            # Uncomment when using an Ingress with TLS:
            # - name: HOUNDARR_SECURE_COOKIES
            #   value: "true"
            # - name: HOUNDARR_TRUSTED_PROXIES
            #   value: "10.244.0.0/16" # your ingress controller pod CIDR
          volumeMounts:
            - name: data
              mountPath: /data
          livenessProbe:
            httpGet:
              path: /api/health
              port: http
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /api/health
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            requests:
              memory: "64Mi"
              cpu: "100m"
            limits:
              memory: "256Mi"
              cpu: "500m"
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 1Gi
```

The `volumeClaimTemplates` block creates a PVC automatically. The StatefulSet
manages its lifecycle — the PVC persists even if the pod is deleted or
rescheduled.

:::danger
The `/data` volume contains the encryption master key and database. Back it up.
If the master key is lost, all stored API keys become unrecoverable.
:::

### Services

A StatefulSet requires a headless Service for pod DNS. A second ClusterIP
Service provides a stable endpoint for Ingress or direct access.

```yaml
# Headless Service (required by StatefulSet)
apiVersion: v1
kind: Service
metadata:
  name: houndarr
  namespace: houndarr
spec:
  clusterIP: None
  selector:
    app: houndarr
  ports:
    - port: 8877
      targetPort: http
---
# ClusterIP Service (for Ingress or port-forwarding)
apiVersion: v1
kind: Service
metadata:
  name: houndarr-web
  namespace: houndarr
spec:
  selector:
    app: houndarr
  ports:
    - port: 8877
      targetPort: http
```

## Exposing Houndarr

### Port-forwarding (quick access)

```bash
kubectl port-forward -n houndarr svc/houndarr-web 8877:8877
```

Then open `http://localhost:8877`.

### Ingress

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: houndarr
  namespace: houndarr
spec:
  rules:
    - host: houndarr.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: houndarr-web
                port:
                  number: 8877
  tls:
    - hosts:
        - houndarr.example.com
      secretName: houndarr-tls
```

When using TLS, uncomment the security env vars in the StatefulSet:

- `HOUNDARR_SECURE_COOKIES=true` — marks session cookies as HTTPS-only
- `HOUNDARR_TRUSTED_PROXIES` — set to your ingress controller's pod CIDR so
  the rate limiter sees real client IPs

See [Environment Variables](/docs/configuration/environment-variables) and
[Trust & Security](/docs/security/trust-and-security) for details.

## Verifying the deployment

```bash
# Check the pod is running
kubectl get pods -n houndarr

# View logs
kubectl logs -n houndarr houndarr-0

# Test the health endpoint
kubectl exec -n houndarr houndarr-0 -- wget -qO- http://localhost:8877/api/health
# Should return: {"status":"ok"}
```

## Helm

There is no official Helm chart yet. If you'd like to contribute one, [open an
issue on GitHub](https://github.com/av1155/houndarr/issues).
