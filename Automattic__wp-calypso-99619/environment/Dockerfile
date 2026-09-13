FROM node:20-bookworm@sha256:8f693eaa7e0a8e71560c9a82b55fd54c2ae920a2ba5d2cde28bac7d1c01c9ba5
USER root
RUN apt-get update && apt-get install -y --no-install-recommends git bash python3 ca-certificates && rm -rf /var/lib/apt/lists/*
RUN python3 -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11+ is required by the verifier'"
ENV TZ=UTC LANG=C.UTF-8 CI=true
COPY bootstrap.sh /tmp/iid-bootstrap.sh
RUN bash /tmp/iid-bootstrap.sh && rm /tmp/iid-bootstrap.sh
WORKDIR /testbed
COPY base.tar /tmp/iid-base.tar
RUN tar -xf /tmp/iid-base.tar -C /testbed && rm /tmp/iid-base.tar && git init && git config core.autocrlf false && git add --force . && git -c user.name=IID -c user.email=iid@example.invalid commit -m "base snapshot"
COPY setup.sh /tmp/iid-setup.sh
RUN bash /tmp/iid-setup.sh && git restore --worktree --staged . && rm /tmp/iid-setup.sh
COPY assets/Automattic__wp-calypso-99619/ /testbed/assets/Automattic__wp-calypso-99619/
RUN printf '\n/assets/Automattic__wp-calypso-99619/\n/.iid-results/\n' >> /testbed/.git/info/exclude
WORKDIR /testbed
