// Copyright (c) 2025 AUTHORS All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.

package main

import (
	"bytes"
	"context"
	"errors"
	"io"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"tailscale.com/ipn/ipnstate"
	"tailscale.com/types/views"
)

func TestBrowserEnrollment(t *testing.T) {
	tags := views.SliceOf([]string{"tag:catch"})
	login := &ipnstate.Status{AuthURL: "https://login.tailscale.com/a/example"}
	states := []*ipnstate.Status{{}, login, login, {BackendState: "Running", Self: &ipnstate.PeerStatus{DNSName: "example.ts.net.", Tags: &tags}}}
	ticks := make(chan time.Time)
	close(ticks)
	var result bytes.Buffer
	err := waitForEnrollment(t.Context(), func(context.Context) (*ipnstate.Status, error) { s := states[0]; states = states[1:]; return s, nil }, false, &result, ticks)
	if err != nil || result.String() != "{\"url\":\"https://login.tailscale.com/a/example\"}\n{\"ready\":\"example.ts.net\"}\n" {
		t.Fatalf("events = %s, err %v", result.String(), err)
	}
	if _, err := enrollmentEvent(login, "", true); err == nil {
		t.Fatal("accepted interactive login for a preseeded key")
	}
	if _, err := enrollmentEvent(&ipnstate.Status{BackendState: "Running", Self: &ipnstate.PeerStatus{}}, "", false); err == nil {
		t.Fatal("accepted untagged host")
	}
}

func TestEnrollmentErrors(t *testing.T) {
	root := t.TempDir()
	key := filepath.Join(root, "key")
	if err := enroll(t.Context(), root, "", key); err == nil {
		t.Fatal("accepted empty hostname")
	}
	if err := enroll(t.Context(), root, "host", key); !errors.Is(err, os.ErrNotExist) {
		t.Fatal(err)
	}
	if err := os.WriteFile(key, []byte(" \n"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := authKey(key); err == nil {
		t.Fatal("accepted empty key")
	}
	if err := os.WriteFile(key, []byte(" test-key\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if value, err := authKey(key); value != "test-key" || err != nil {
		t.Fatal(value, err)
	}
	if value, err := authKey(""); value != "" || err != nil {
		t.Fatal(value, err)
	}
	err := waitForEnrollment(t.Context(), func(context.Context) (*ipnstate.Status, error) { return nil, errors.New("offline") }, false, &bytes.Buffer{}, nil)
	if err == nil || !strings.Contains(err.Error(), "offline") {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	err = waitForEnrollment(ctx, func(context.Context) (*ipnstate.Status, error) { return &ipnstate.Status{}, nil }, false, &bytes.Buffer{}, nil)
	if !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
}

func TestInstallerProxyRelaysSession(t *testing.T) {
	upstream, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer upstream.Close()
	testConnection, exposed := net.Pipe()
	defer testConnection.Close()
	if err := testConnection.SetDeadline(time.Now().Add(5 * time.Second)); err != nil {
		t.Fatal(err)
	}
	go forwardInstallerSSH(t.Context(), exposed, upstream.Addr().String())
	server, err := upstream.Accept()
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	go func() { _, _ = io.Copy(server, server) }()
	if _, err := io.WriteString(testConnection, "session"); err != nil {
		t.Fatal(err)
	}
	reply := make([]byte, len("session"))
	if _, err := io.ReadFull(testConnection, reply); err != nil || string(reply) != "session" {
		t.Fatalf("reply %q, err %v", reply, err)
	}
}

type installerTestNetwork struct {
	status   *ipnstate.Status
	listener net.Listener
	ready    chan struct{}
}

func (n installerTestNetwork) Up(context.Context) (*ipnstate.Status, error) { return n.status, nil }
func (n installerTestNetwork) Listen(string, string) (net.Listener, error) {
	close(n.ready)
	return n.listener, nil
}

func TestInstallerPublishesTaggedEndpointUntilShutdown(t *testing.T) {
	dir := t.TempDir()
	state := filepath.Join(dir, "tsnet")
	if err := serveInstaller(t.Context(), state, "lab", filepath.Join(dir, "missing")); err == nil {
		t.Fatal("missing enrollment key accepted")
	}
	if err := acceptInstallerSessions(t.Context(), state, installerTestNetwork{status: &ipnstate.Status{}}); err == nil {
		t.Fatal("untagged installer accepted")
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	ready := make(chan struct{})
	tags := views.SliceOf([]string{"tag:catch"})
	status := &ipnstate.Status{Self: &ipnstate.PeerStatus{DNSName: "lab.test.", Tags: &tags}}
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- acceptInstallerSessions(ctx, state, installerTestNetwork{status, listener, ready}) }()
	<-ready
	var name []byte
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); {
		name, err = os.ReadFile(filepath.Join(dir, "ready"))
		if err == nil && len(name) > 0 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if err != nil || string(name) != "lab.test\n" {
		t.Fatalf("installer readiness = %q, %v", name, err)
	}
	cancel()
	if err := <-done; !errors.Is(err, net.ErrClosed) {
		t.Fatalf("shutdown: %v", err)
	}
	if _, err := os.Stat(filepath.Join(dir, "ready")); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("stale readiness file: %v", err)
	}
}
