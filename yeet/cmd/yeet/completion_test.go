// Copyright (c) 2025 AUTHORS All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.

package main

import (
	"slices"
	"testing"
)

func TestCompleteWords(t *testing.T) {
	for _, tc := range []struct {
		words  []string
		want   []string
		lookup bool
	}{
		{[]string{"stat"}, []string{"status"}, false},
		{[]string{"_vm"}, nil, false},
		{[]string{"--host", "localhost", "service", "gener"}, []string{"generations"}, false},
		{[]string{"status", "--fo"}, []string{"--format"}, false},
		{[]string{"config", "--add-wo"}, []string{"--add-workspace"}, false},
		{[]string{"run", "--publish-res"}, []string{"--publish-reset"}, false},
		{[]string{"--host=localhost", "restart", "web"}, []string{"web-one", "web-two"}, true},
		{[]string{"--host=localhost", "status", "web-one", "web-t"}, []string{"web-two"}, true},
		{[]string{"--host=elsewhere", "restart", "web"}, nil, false},
		{[]string{"--host=localhost", "run", "web", "./"}, nil, false},
		{[]string{"--host=localhost", "ssh", "--", "web"}, nil, false},
	} {
		called := false
		got := completeWords(tc.words, func() []string { called = true; return []string{"db", "web-two", "web-one"} })
		if !slices.Equal(got, tc.want) || called != tc.lookup {
			t.Errorf("%q: candidates=%q lookup=%v, want %q lookup=%v", tc.words, got, called, tc.want, tc.lookup)
		}
	}
}
