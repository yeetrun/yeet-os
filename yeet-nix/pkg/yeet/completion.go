// Copyright (c) 2026 AUTHORS All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.

package yeet

import "github.com/shayne/yargs"

// LocalCompletionFlags exposes the flag schemas owned by the workstation commands.
func LocalCompletionFlags(command string) map[string]bool {
	switch command {
	case "init":
		return yargs.CompletionFlags(initFlagsParsed{})
	case "config":
		return yargs.CompletionFlags(configFlagsParsed{})
	}
	return nil
}
