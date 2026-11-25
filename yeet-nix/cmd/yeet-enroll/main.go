// Copyright (c) 2025 AUTHORS All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.

// yeet-enroll registers the same persistent tsnet identity that Catch opens at boot.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"tailscale.com/ipn/ipnstate"
	"tailscale.com/tsnet"
)

func main() {
	state := flag.String("state", "", "directory for the Yeet host’s Tailscale identity")
	host := flag.String("hostname", "", "tailnet hostname")
	key := flag.String("key-file", "", "file containing a tagged auth key")
	live := flag.Bool("installer-ssh", false, "forward tailnet SSH to the live installer")
	flag.Parse()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if *live {
		if err := serveInstaller(ctx, *state, *host, *key); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if *key != "" {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, 5*time.Minute)
		defer cancel()
	}
	if err := enroll(ctx, *state, *host, *key); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func enroll(ctx context.Context, state, host, keyFile string) error {
	if state == "" || host == "" {
		return errors.New("hostname and state directory are required")
	}
	if err := os.MkdirAll(state, 0700); err != nil {
		return err
	}
	server := &tsnet.Server{Dir: state, Hostname: host, AdvertiseTags: []string{"tag:catch"}, UserLogf: func(string, ...any) {}}
	key, err := authKey(keyFile)
	if err != nil {
		return err
	}
	server.AuthKey = key
	if err := server.Start(); err != nil {
		return err
	}
	defer func() { _ = server.Close() }()
	client, err := server.LocalClient()
	if err != nil {
		return err
	}
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	return waitForEnrollment(ctx, client.StatusWithoutPeers, keyFile != "", os.Stdout, ticker.C)
}

func waitForEnrollment(ctx context.Context, statusFn func(context.Context) (*ipnstate.Status, error), keyed bool, writer io.Writer, ticks <-chan time.Time) error {
	lastURL := ""
	output := json.NewEncoder(writer)
	for {
		status, err := statusFn(ctx)
		if err != nil {
			return err
		}
		event, err := enrollmentEvent(status, lastURL, keyed)
		if err != nil {
			return err
		}
		if event != nil {
			if err := output.Encode(event); err != nil {
				return err
			}
			if _, done := event["ready"]; done {
				return nil
			}
			lastURL = event["url"]
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticks:
		}
	}
}

func authKey(path string) (string, error) {
	if path == "" {
		return "", nil
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	key := strings.TrimSpace(string(raw))
	if key == "" {
		return "", errors.New("the Tailscale auth key file is empty")
	}
	return key, nil
}

func enrollmentEvent(status *ipnstate.Status, lastURL string, keyed bool) (map[string]string, error) {
	if status.BackendState == "Running" && status.Self != nil {
		if !status.Self.IsTagged() {
			return nil, errors.New("apply tag:catch in the Tailscale admin console, then retry enrollment")
		}
		return map[string]string{"ready": strings.TrimSuffix(status.Self.DNSName, ".")}, nil
	}
	if status.AuthURL == "" || status.AuthURL == lastURL {
		return nil, nil
	}
	if keyed {
		return nil, errors.New("the key did not enroll the host; check its expiry, tag and device approval")
	}
	return map[string]string{"url": status.AuthURL}, nil
}
