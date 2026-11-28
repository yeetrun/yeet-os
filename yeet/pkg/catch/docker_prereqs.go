// Copyright (c) 2025 AUTHORS All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.

package catch

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"

	"github.com/yeetrun/yeet/pkg/db"
	"github.com/yeetrun/yeet/pkg/svc"
)

const (
	dockerPrereqsTargetUnit = "yeet-docker-prereqs.target"
	dockerServiceUnit       = "docker.service"
	dockerPluginSocket      = "/run/docker/plugins/yeet.sock"
)

var installDockerPrereqs = func(s *Server) error {
	units, err := s.dockerNetNSServiceUnits()
	if err != nil {
		return err
	}
	return dockerPrereqsInstaller{root: "/"}.install(units)
}

type dockerPrereqsInstaller struct {
	root         string
	runSystemctl func(args ...string) error
}

func (s *Server) dockerNetNSServiceUnits() ([]string, error) {
	dv, err := s.getDB()
	if err != nil {
		return nil, err
	}

	var units []string
	for _, sv := range dv.Services().All() {
		if sv.ServiceType() != db.ServiceTypeDockerCompose {
			continue
		}
		service := sv.AsStruct()
		if _, ok := service.Artifacts.Gen(db.ArtifactNetNSService, service.Generation); !ok {
			continue
		}
		units = append(units, serviceNetNSUnitName(sv.Name()))
	}
	return sortedUniqueUnits(units), nil
}

func serviceNetNSUnitName(serviceName string) string {
	return "yeet-" + serviceName + "-ns.service"
}

func dockerPluginSocketWaitCommand() string {
	return "/bin/sh -c 'i=0; while [ \"$i\" -lt 600 ]; do [ -S " + dockerPluginSocket + " ] && exit 0; i=$((i+1)); sleep 0.1; done; exit 1'"
}

// NixOS owns the target and Docker ordering. Only its payload dependencies vary.
func dockerPayloadDependencies(serviceUnits []string) string {
	units := strings.Join(sortedUniqueUnits(serviceUnits), " ")
	if units == "" {
		return "[Unit]\n"
	}
	return fmt.Sprintf("[Unit]\nWants=%s\nAfter=%s\n", units, units)
}

func (i dockerPrereqsInstaller) install(serviceUnits []string) error {
	run := i.runSystemctl
	if run == nil {
		run = defaultRunSystemctl
	}

	path := i.path(svc.SystemdUnitDirectory, dockerPrereqsTargetUnit+".d", "payloads.conf")
	changed, err := writeTextFileIfChanged(path, dockerPayloadDependencies(serviceUnits), 0644)
	if err != nil {
		return err
	}

	if changed {
		if err := run("daemon-reload"); err != nil {
			return fmt.Errorf("reload systemd after installing Docker prerequisites: %w", err)
		}
	}
	return nil
}

func (i dockerPrereqsInstaller) path(elem ...string) string {
	root := i.root
	if root == "" {
		root = "/"
	}
	return filepath.Join(append([]string{root}, elem...)...)
}

func defaultRunSystemctl(args ...string) error {
	cmd := exec.Command("systemctl", args...)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("systemctl %s: %w\n%s", strings.Join(args, " "), err, string(output))
	}
	return nil
}

func writeTextFileIfChanged(path, content string, perm os.FileMode) (bool, error) {
	raw := []byte(content)
	same, err := textFileContentMatches(path, raw)
	if err != nil {
		return false, err
	}
	if same {
		return false, nil
	}
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return false, fmt.Errorf("create parent dir for %s: %w", path, err)
	}
	if err := writeTextFileAtomically(path, raw, perm); err != nil {
		return false, err
	}
	return true, nil
}

func textFileContentMatches(path string, raw []byte) (bool, error) {
	prev, err := os.ReadFile(path)
	if err == nil && bytes.Equal(prev, raw) {
		return true, nil
	}
	if err != nil && !os.IsNotExist(err) {
		return false, fmt.Errorf("read %s: %w", path, err)
	}
	return false, nil
}

func writeTextFileAtomically(path string, raw []byte, perm os.FileMode) (err error) {
	tmp, err := os.CreateTemp(filepath.Dir(path), filepath.Base(path)+".tmp.")
	if err != nil {
		return fmt.Errorf("create temp file for %s: %w", path, err)
	}
	atomicFile := &atomicTextFile{path: path, tmpName: tmp.Name(), file: tmp}
	defer atomicFile.cleanup(&err)
	if err := atomicFile.write(raw, perm); err != nil {
		return err
	}
	if err := atomicFile.close(); err != nil {
		return err
	}
	return atomicFile.replace()
}

type atomicTextFile struct {
	path    string
	tmpName string
	file    *os.File
	closed  bool
}

func (f *atomicTextFile) write(raw []byte, perm os.FileMode) error {
	if _, err := f.file.Write(raw); err != nil {
		return fmt.Errorf("write temp file for %s: %w", f.path, err)
	}
	if err := f.file.Chmod(perm); err != nil {
		return fmt.Errorf("chmod temp file for %s: %w", f.path, err)
	}
	return nil
}

func (f *atomicTextFile) close() error {
	err := f.file.Close()
	f.closed = true
	if err != nil {
		return fmt.Errorf("close temp file for %s: %w", f.path, err)
	}
	return nil
}

func (f *atomicTextFile) replace() error {
	if err := os.Rename(f.tmpName, f.path); err != nil {
		return fmt.Errorf("replace %s: %w", f.path, err)
	}
	return nil
}

func (f *atomicTextFile) cleanup(errp *error) {
	f.closeIfOpen(errp)
	f.removeOnError(errp)
}

func (f *atomicTextFile) closeIfOpen(errp *error) {
	if f.closed {
		return
	}
	if err := f.file.Close(); err != nil {
		*errp = errors.Join(*errp, fmt.Errorf("close temp file for %s: %w", f.path, err))
	}
	f.closed = true
}

func (f *atomicTextFile) removeOnError(errp *error) {
	if *errp == nil {
		return
	}
	if err := os.Remove(f.tmpName); err != nil && !os.IsNotExist(err) {
		*errp = errors.Join(*errp, fmt.Errorf("remove temp file for %s: %w", f.path, err))
	}
}

func sortedUniqueUnits(units []string) []string {
	if len(units) == 0 {
		return nil
	}
	units = append([]string(nil), units...)
	sort.Strings(units)
	uniq := units[:0]
	for _, unit := range units {
		if unit == "" {
			continue
		}
		if len(uniq) == 0 || uniq[len(uniq)-1] != unit {
			uniq = append(uniq, unit)
		}
	}
	return uniq
}
