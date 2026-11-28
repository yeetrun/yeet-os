// Copyright (c) 2025 AUTHORS All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.

package netns

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/yeetrun/yeet/pkg/db"
)

func TestWriteServiceNetNSOrdersBeforeDockerPrereqs(t *testing.T) {
	root := t.TempDir()
	oldwd, err := os.Getwd()
	if err != nil {
		t.Fatalf("Getwd returned error: %v", err)
	}
	if err := os.Chdir(root); err != nil {
		t.Fatalf("Chdir returned error: %v", err)
	}
	t.Cleanup(func() {
		if err := os.Chdir(oldwd); err != nil {
			t.Fatalf("restore Chdir returned error: %v", err)
		}
	})

	binDir := filepath.Join(root, "bin")
	runDir := filepath.Join(root, "run")
	if err := os.MkdirAll(binDir, 0755); err != nil {
		t.Fatalf("MkdirAll binDir returned error: %v", err)
	}
	if err := os.MkdirAll(runDir, 0755); err != nil {
		t.Fatalf("MkdirAll runDir returned error: %v", err)
	}

	artifacts, err := WriteServiceNetNS(binDir, runDir, Service{ServiceName: "media"})
	if err != nil {
		t.Fatalf("WriteServiceNetNS returned error: %v", err)
	}
	raw, err := os.ReadFile(artifacts[db.ArtifactNetNSService])
	if err != nil {
		t.Fatalf("ReadFile returned error: %v", err)
	}
	got := string(raw)
	for _, want := range []string{
		"Requires=yeet-ns.service\n",
		"After=yeet-ns.service\n",
		"Before=yeet-docker-prereqs.target docker.service\n",
		"WantedBy=multi-user.target yeet-docker-prereqs.target\n",
		"EnvironmentFile=" + filepath.Join(root, "env", "netns.env") + "\n",
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("unit missing %q:\n%s", want, got)
		}
	}
}

func TestWriteServiceNetNSWaitsForNetworkOnlineForMacvlan(t *testing.T) {
	root := t.TempDir()
	oldwd, err := os.Getwd()
	if err != nil {
		t.Fatalf("Getwd returned error: %v", err)
	}
	if err := os.Chdir(root); err != nil {
		t.Fatalf("Chdir returned error: %v", err)
	}
	t.Cleanup(func() {
		if err := os.Chdir(oldwd); err != nil {
			t.Fatalf("restore Chdir returned error: %v", err)
		}
	})

	binDir := filepath.Join(root, "bin")
	runDir := filepath.Join(root, "run")
	if err := os.MkdirAll(binDir, 0755); err != nil {
		t.Fatalf("MkdirAll binDir returned error: %v", err)
	}
	if err := os.MkdirAll(runDir, 0755); err != nil {
		t.Fatalf("MkdirAll runDir returned error: %v", err)
	}

	artifacts, err := WriteServiceNetNS(binDir, runDir, Service{
		ServiceName:   "media",
		MacvlanParent: "vmbr0",
	})
	if err != nil {
		t.Fatalf("WriteServiceNetNS returned error: %v", err)
	}
	raw, err := os.ReadFile(artifacts[db.ArtifactNetNSService])
	if err != nil {
		t.Fatalf("ReadFile returned error: %v", err)
	}
	got := string(raw)
	for _, want := range []string{
		"Wants=network-online.target\n",
		"Requires=yeet-ns.service\n",
		"After=yeet-ns.service network-online.target\n",
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("unit missing %q:\n%s", want, got)
		}
	}
}

func TestWriteServiceNetNSRequiresTailscaleUnit(t *testing.T) {
	root := t.TempDir()
	oldwd, err := os.Getwd()
	if err != nil {
		t.Fatalf("Getwd returned error: %v", err)
	}
	if err := os.Chdir(root); err != nil {
		t.Fatalf("Chdir returned error: %v", err)
	}
	t.Cleanup(func() {
		if err := os.Chdir(oldwd); err != nil {
			t.Fatalf("restore Chdir returned error: %v", err)
		}
	})

	binDir := filepath.Join(root, "bin")
	runDir := filepath.Join(root, "run")
	if err := os.MkdirAll(binDir, 0755); err != nil {
		t.Fatalf("MkdirAll binDir returned error: %v", err)
	}
	if err := os.MkdirAll(runDir, 0755); err != nil {
		t.Fatalf("MkdirAll runDir returned error: %v", err)
	}

	artifacts, err := WriteServiceNetNS(binDir, runDir, Service{
		ServiceName:           "media",
		TailscaleTAPInterface: "tap0",
	})
	if err != nil {
		t.Fatalf("WriteServiceNetNS returned error: %v", err)
	}
	raw, err := os.ReadFile(artifacts[db.ArtifactNetNSService])
	if err != nil {
		t.Fatalf("ReadFile returned error: %v", err)
	}
	got := string(raw)
	for _, want := range []string{
		"Requires=yeet-ns.service yeet-media-ts.service\n",
		"After=yeet-ns.service yeet-media-ts.service\n",
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("unit missing %q:\n%s", want, got)
		}
	}
	if svc := (&Service{ServiceName: "media"}).ServiceUnit(); svc != "yeet-media-ns.service" {
		t.Fatalf("ServiceUnit = %q, want yeet-media-ns.service", svc)
	}
}

func TestServiceNSScriptUsesNamespaceResolver(t *testing.T) {
	raw, err := os.ReadFile("netns-scripts/service-ns")
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{
		`resolver=/run/systemd/resolve/resolv.conf`,
		`cp "$resolver" "$NETNS_ETC_DIR/$NS_NAME/resolv.conf"`,
		`dhcpcd --nohook resolv.conf "$MACVLAN_INTERFACE"`,
		`cp "$RESOLV_CONF" "$NETNS_ETC_DIR/$NS_NAME/resolv.conf"`,
	} {
		if !strings.Contains(string(raw), want) {
			t.Errorf("service-ns missing %q", want)
		}
	}
}

func TestServiceNSScriptCleanupRemovesResolverDirectory(t *testing.T) {
	resolverDir := runServiceNSScriptCleanup(t, map[string]string{
		"resolv.conf": "nameserver 192.0.2.53\n",
	})

	if _, err := os.Stat(resolverDir); !os.IsNotExist(err) {
		t.Fatalf("resolver directory stat error = %v, want not exist", err)
	}
}

func TestServiceNSScriptCleanupPreservesUnexpectedFiles(t *testing.T) {
	resolverDir := runServiceNSScriptCleanup(t, map[string]string{
		"keep":        "operator state\n",
		"resolv.conf": "nameserver 192.0.2.53\n",
	})

	if _, err := os.Stat(filepath.Join(resolverDir, "resolv.conf")); !os.IsNotExist(err) {
		t.Fatalf("resolv.conf stat error = %v, want not exist", err)
	}
	got, err := os.ReadFile(filepath.Join(resolverDir, "keep"))
	if err != nil {
		t.Fatalf("ReadFile keep returned error: %v", err)
	}
	if string(got) != "operator state\n" {
		t.Fatalf("keep content = %q, want %q", got, "operator state\n")
	}
}

func runServiceNSScriptCleanup(t *testing.T, resolverFiles map[string]string) string {
	t.Helper()

	raw, err := os.ReadFile("netns-scripts/service-ns")
	if err != nil {
		t.Fatalf("ReadFile embedded service-ns returned error: %v", err)
	}
	root := t.TempDir()
	scriptPath := filepath.Join(root, "service-ns")
	if err := os.WriteFile(scriptPath, raw, 0755); err != nil {
		t.Fatalf("WriteFile service-ns returned error: %v", err)
	}
	fakeBinDir := filepath.Join(root, "bin")
	if err := os.Mkdir(fakeBinDir, 0755); err != nil {
		t.Fatalf("Mkdir fake bin returned error: %v", err)
	}
	if err := os.WriteFile(filepath.Join(fakeBinDir, "ip"), []byte("#!/bin/sh\nexit 0\n"), 0755); err != nil {
		t.Fatalf("WriteFile fake ip returned error: %v", err)
	}

	netNSEtcDir := filepath.Join(root, "etc-netns")
	resolverDir := filepath.Join(netNSEtcDir, "yeet-cleanup-test-ns")
	if err := os.MkdirAll(resolverDir, 0755); err != nil {
		t.Fatalf("MkdirAll resolver directory returned error: %v", err)
	}
	for name, content := range resolverFiles {
		if err := os.WriteFile(filepath.Join(resolverDir, name), []byte(content), 0644); err != nil {
			t.Fatalf("WriteFile resolver file %q returned error: %v", name, err)
		}
	}

	cmd := exec.Command(scriptPath, "cleanup")
	cmd.Env = append(os.Environ(),
		"SERVICE_NAME=cleanup-test",
		"NETNS_ETC_DIR="+netNSEtcDir,
		"PATH="+fakeBinDir+string(os.PathListSeparator)+os.Getenv("PATH"),
	)
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("service-ns cleanup returned error: %v\n%s", err, output)
	}
	return resolverDir
}

func TestServiceNSScriptPinsServiceNetworkRoutes(t *testing.T) {
	raw, err := os.ReadFile("netns-scripts/service-ns")
	if err != nil {
		t.Fatalf("ReadFile embedded service-ns returned error: %v", err)
	}
	got := string(raw)

	for _, want := range []string{
		`ip netns exec $NS_NAME ip route replace "$RANGE" dev "$IF_IN_NS_NAME"`,
		`ip netns exec $NS_NAME ip route replace "$HOST_IP/32" dev "$IF_IN_NS_NAME"`,
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("service-ns missing %q:\n%s", want, got)
		}
	}
}

func TestYeetNSScriptConnectsHostPeerToServiceBridge(t *testing.T) {
	raw, err := os.ReadFile("netns-scripts/yeet-ns")
	if err != nil {
		t.Fatalf("ReadFile yeet-ns script returned error: %v", err)
	}
	script := string(raw)
	for _, want := range []string{
		"if ! ip netns list | grep -q yeet-ns; then",
		"if ! ip netns exec yeet-ns ip link show br0; then",
		"if ! ip link show yeet0; then",
		"ip netns exec yeet-ns ip link set yeet0-peer master br0",
		"ip netns exec yeet-ns ip addr replace ${YEET_IP} dev br0",
		"ip netns exec yeet-ns ip addr replace ${BRIDGE_IP} dev br0",
		"ip netns exec yeet-ns ip route replace ${HOST_IP} dev br0",
		"ip netns exec yeet-ns ip route replace default via ${HOST_IP_BASE} dev br0",
		"ip route replace ${RANGE} dev ${BRIDGE_IF}",
		`"${CATCH_BIN}" netns-firewall ensure`,
		`"${CATCH_BIN}" netns-firewall verify`,
	} {
		if !strings.Contains(script, want) {
			t.Fatalf("yeet-ns script missing %q:\n%s", want, script)
		}
	}
	for _, bad := range []string{
		"ip netns exec yeet-ns ip addr replace ${YEET_IP} dev yeet0-peer",
		"ip route replace ${RANGE} via ${YEET_IP_BASE} dev ${BRIDGE_IF}",
		"ip route del ${RANGE}",
	} {
		if strings.Contains(script, bad) {
			t.Fatalf("yeet-ns script contains obsolete routed host peer command %q:\n%s", bad, script)
		}
	}
}
