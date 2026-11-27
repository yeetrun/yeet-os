// Copyright (c) 2026 AUTHORS All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.

package main

import (
	"context"
	"strings"
	"time"

	"github.com/shayne/yargs"
	"github.com/yeetrun/yeet/pkg/catchrpc"
	"github.com/yeetrun/yeet/pkg/cli"
	"github.com/yeetrun/yeet/pkg/yeet"
)

func completionFlags(path []string) map[string]bool {
	var specs map[string]cli.FlagSpec
	switch len(path) {
	case 0:
		return yargs.CompletionFlags(globalFlagsParsed{})
	case 1:
		switch path[0] {
		case "list-hosts":
			return yargs.CompletionFlags(listHostsFlagsParsed{})
		case "config", "init":
			return yeet.LocalCompletionFlags(path[0])
		case "ssh", "copy":
			return map[string]bool{"--force-proxy": false}
		case "upgrade":
			return yargs.CompletionFlags(cli.UpgradeFlags{})
		}
		specs = cli.RemoteFlagSpecs()[path[0]]
	case 2:
		specs = cli.RemoteGroupFlagSpecs()[path[0]][path[1]]
	}
	flags := make(map[string]bool, len(specs))
	for name, spec := range specs {
		flags[name] = spec.ConsumesValue
	}
	return flags
}

func servicePosition(path []string, position int) bool {
	if len(path) == 1 {
		switch path[0] {
		case "status":
			return true
		case "ssh", "events", "logs":
			return position == 0
		}
	}
	spec, ok := cli.RemoteCommandRegistry().CommandSpec(path)
	if ok {
		for _, arg := range yargs.ExtractArgSpecs(spec.ArgsSchema) {
			if (arg.Position == position || arg.Variadic && arg.Position < position) && cli.IsServiceArgSpec(arg) {
				return true
			}
		}
	}
	return false
}

func completeWords(words []string, listLocal func() []string) []string {
	return buildHelpConfig().Complete(words, yargs.CompletionOptions{
		Flags: completionFlags,
		Argument: func(path []string, position int, prefix string) []string {
			if !servicePosition(path, position) || strings.ContainsAny(prefix, "/@") {
				return nil
			}
			flags, _, err := parseGlobalFlags(words[:len(words)-1])
			if err != nil {
				return nil
			}
			host := flags.Host
			if host == "" {
				host = yeet.Host()
			}
			if host == "localhost" {
				return listLocal()
			}
			return nil
		},
	})
}

// The host already exposes a read-only inventory method on its local admin socket.
// Keep Tab responsive when the daemon is starting or busy.
func localServiceNames() []string {
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	var services []struct {
		Name string `json:"name"`
	}
	if err := catchrpc.NewUnixClient(catchrpc.LocalSocket).Call(ctx, "catch.ServicesList", nil, &services); err != nil {
		return nil
	}
	var names []string
	for _, svc := range services {
		names = append(names, svc.Name)
	}
	return names
}
