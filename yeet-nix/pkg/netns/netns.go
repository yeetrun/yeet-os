// Copyright (c) 2025 AUTHORS All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.

package netns

import (
	"fmt"
	"net/netip"
	"path/filepath"

	"github.com/yeetrun/yeet/pkg/db"
	"github.com/yeetrun/yeet/pkg/env"
	"github.com/yeetrun/yeet/pkg/fileutil"
	"github.com/yeetrun/yeet/pkg/svc"
)

const (
	dockerPrereqsTargetUnit = "yeet-docker-prereqs.target"
	dockerServiceUnit       = "docker.service"
)

const (
	ServiceSubnetCIDR = "192.168.100.0/24"
	ServiceHostIP     = "192.168.100.1"
	ServiceYeetNSIP   = "192.168.100.2"
	ServiceGatewayIP  = "192.168.100.254"
)

type Service struct {
	ServiceName string       `env:"SERVICE_NAME"`
	ServiceIP   netip.Prefix `env:"SERVICE_IP"`
	Range       netip.Prefix `env:"RANGE"`
	HostIP      netip.Addr   `env:"HOST_IP"`
	YeetIP      netip.Addr   `env:"YEET_IP"`

	MacvlanParent    string `env:"MACVLAN_PARENT"`
	MacvlanVLAN      string `env:"MACVLAN_VLAN"`
	MacvlanMac       string `env:"MACVLAN_MAC"`
	MacvlanInterface string `env:"MACVLAN_INTERFACE"`

	TailscaleTAPInterface string `env:"TAILSCALE_TAP_INTERFACE"`

	ResolvConf string `env:"RESOLV_CONF"`
}

func (e *Service) NetNS() string {
	return "yeet-" + e.ServiceName + "-ns"
}

func (e *Service) ServiceUnit() string {
	return e.NetNS() + ".service"
}

func appendSystemdDep(existing, dep string) string {
	if existing == "" {
		return dep
	}
	return existing + " " + dep
}

func WriteServiceNetNS(binDir, runDir string, se Service) (map[db.ArtifactName]string, error) {
	envFile := filepath.Join(binDir, fileutil.ApplyVersion("netns.env"))
	if err := env.Write(envFile, se); err != nil {
		return nil, fmt.Errorf("failed to write env: %v", err)
	}

	exe := "/etc/yeet-os/service-ns"
	unit := svc.SystemdUnit{
		Name:             se.NetNS(),
		Executable:       exe,
		EnvFile:          filepath.Join(filepath.Dir(runDir), "env", "netns.env"),
		WorkingDirectory: "/",
		Requires:         "yeet-ns.service",
		After:            "yeet-ns.service",
		Before:           dockerPrereqsTargetUnit + " " + dockerServiceUnit,
		OneShot:          true,
		StopCmd:          exe + " cleanup",
		WantedBy:         "multi-user.target " + dockerPrereqsTargetUnit,
	}
	if se.MacvlanParent != "" {
		unit.Wants = appendSystemdDep(unit.Wants, "network-online.target")
		unit.After = appendSystemdDep(unit.After, "network-online.target")
	}
	if se.TailscaleTAPInterface != "" {
		tsUnit := "yeet-" + se.ServiceName + "-ts.service"
		unit.Requires = appendSystemdDep(unit.Requires, tsUnit)
		unit.After = appendSystemdDep(unit.After, tsUnit)
	}
	artifacts, err := unit.WriteOutUnitFiles(binDir)
	if err != nil {
		return nil, fmt.Errorf("failed to write unit files: %v", err)
	}
	artifacts[db.ArtifactNetNSService] = artifacts[db.ArtifactSystemdUnit]
	delete(artifacts, db.ArtifactSystemdUnit)
	artifacts[db.ArtifactNetNSEnv] = envFile
	return artifacts, nil
}
