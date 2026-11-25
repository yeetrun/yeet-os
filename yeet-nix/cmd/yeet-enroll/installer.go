// Copyright (c) 2025 AUTHORS All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.

package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"strings"
	"time"

	"tailscale.com/ipn/ipnstate"
	"tailscale.com/tsnet"
)

// serveInstaller exposes OpenSSH only inside the enrolled userspace network.
// The running node's state can be handed to Catch after disk installation.
func serveInstaller(ctx context.Context, state, host, keyFile string) error {
	key, err := authKey(keyFile)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(state, 0700); err != nil {
		return err
	}
	s := &tsnet.Server{Dir: state, Hostname: host, AdvertiseTags: []string{"tag:catch"}, AuthKey: key, UserLogf: func(string, ...any) {}}
	defer func() { _ = s.Close() }()
	return acceptInstallerSessions(ctx, state, s)
}

type installerNetwork interface {
	Up(context.Context) (*ipnstate.Status, error)
	Listen(string, string) (net.Listener, error)
}

func acceptInstallerSessions(ctx context.Context, state string, s installerNetwork) error {
	login, cancel := context.WithTimeout(ctx, 3*time.Minute)
	status, err := s.Up(login)
	cancel()
	if err != nil {
		return err
	}
	if status.Self == nil || !status.Self.IsTagged() {
		return errors.New("installer needs a tagged node")
	}
	listener, err := s.Listen("tcp", ":22")
	if err != nil {
		return err
	}
	stop := context.AfterFunc(ctx, func() { _ = listener.Close() })
	defer stop()
	name := strings.TrimSuffix(status.Self.DNSName, ".")
	ready := filepath.Join(filepath.Dir(state), "ready")
	if err := os.WriteFile(ready, []byte(name+"\n"), 0600); err != nil {
		return err
	}
	defer func() { _ = os.Remove(ready) }()
	fmt.Println("Installer SSH is ready on", name)
	for {
		conn, err := listener.Accept()
		if err != nil {
			return err
		}
		go forwardInstallerSSH(ctx, conn, "127.0.0.1:2222")
	}
}

func forwardInstallerSSH(ctx context.Context, conn net.Conn, address string) {
	defer func() { _ = conn.Close() }()
	var dialer net.Dialer
	local, err := dialer.DialContext(ctx, "tcp", address)
	if err != nil {
		return
	}
	defer func() { _ = local.Close() }()
	go func() {
		_, _ = io.Copy(local, conn)
		_ = local.(*net.TCPConn).CloseWrite()
	}()
	_, _ = io.Copy(conn, local)
}
