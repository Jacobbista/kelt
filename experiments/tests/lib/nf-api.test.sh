#!/usr/bin/env bash
# Tests for nf_api_items (lib/common.sh) with kubectl stubbed: the running pod's
# port named "metrics", page by page until the pager's count.
# Run: bash experiments/tests/lib/nf-api.test.sh
set -o pipefail
fails=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fails=$((fails + 1)); }
expect() { if eval "$2"; then ok "$1"; else bad "$1: $3"; fi; }

# shellcheck source=/dev/null
source "$(cd "$(dirname "$0")/../../lib" && pwd)/common.sh"
supis() { python3 -c 'import json,sys; print(",".join(i["supi"] for i in json.load(sys.stdin)["items"]))'; }

# 3 items served 2 per page.
kubectl() {
  case "$*" in
    "get pod -n $CORE_NS -l app=smf"*) echo "smf-abc" ;;
    "get pod -n $CORE_NS smf-abc"*'name=="metrics"'*) echo "9090" ;;
    "get --raw /api/v1/namespaces/$CORE_NS/pods/smf-abc:9090/proxy/pdu-info?page=0&page_size=2")
      echo '{"items":[{"supi":"a"},{"supi":"b"}],"pager":{"page":0,"page_size":2,"count":3}}' ;;
    "get --raw /api/v1/namespaces/$CORE_NS/pods/smf-abc:9090/proxy/pdu-info?page=1&page_size=2")
      echo '{"items":[{"supi":"c"}],"pager":{"page":1,"page_size":2,"count":3}}' ;;
    *) echo "unexpected: $*" >&2 ;;
  esac
}
got="$(NF_API_PAGE_SIZE=2 nf_api_items smf pdu-info 2>&1 | supis 2>&1)"
expect "every page is read" "[ \"\$got\" = a,b,c ]" "$got"

kubectl() { echo ""; }
got="$(nf_api_items amf ue-info)"
expect "no running pod: no items" "[ \"\$got\" = '{\"items\": []}' ]" "$got"

kubectl() { case "$*" in *"-l app=amf"*) echo "amf-1" ;; *"get --raw"*) echo "not json" ;; *) echo "9090" ;; esac; }
got="$(nf_api_items amf ue-info)"
expect "an unreadable page ends the list" "[ \"\$got\" = '{\"items\": []}' ]" "$got"
exit $fails
