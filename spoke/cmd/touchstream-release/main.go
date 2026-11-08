// touchstream-release signs spoke releases, for tools/release (it never goes on a box).
//
//	touchstream-release keygen KEYFILE           make a release key: the private half in KEYFILE (mode 600),
//	                                             the public half printed, for update.PublicKey
//	touchstream-release public KEYFILE           print the public half again
//	touchstream-release sign KEYFILE VERSION FILE    print FILE's SHA-256 and its signature, as JSON
//
// The private key never goes in the repository: tools/release reads it from
// ~/.config/touchstream/release-signing.key (or TOUCHSTREAM_RELEASE_KEY).
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/update"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "touchstream-release:", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) < 2 {
		return errors.New("usage: keygen KEYFILE | public KEYFILE | sign KEYFILE VERSION FILE")
	}
	switch args[0] {
	case "keygen":
		if _, err := os.Stat(args[1]); err == nil {
			return fmt.Errorf("%s is there already: a new key would orphan every spoke that trusts it", args[1])
		}
		_, private, err := ed25519.GenerateKey(rand.Reader)
		if err != nil {
			return err
		}
		if err := os.MkdirAll(filepath.Dir(args[1]), 0o700); err != nil {
			return err
		}
		if err := os.WriteFile(args[1], []byte(base64.StdEncoding.EncodeToString(private.Seed())+"\n"), 0o600); err != nil {
			return err
		}
		fmt.Println(base64.StdEncoding.EncodeToString(private.Public().(ed25519.PublicKey)))
	case "public":
		private, err := load(args[1])
		if err != nil {
			return err
		}
		fmt.Println(base64.StdEncoding.EncodeToString(private.Public().(ed25519.PublicKey)))
	case "sign":
		if len(args) != 4 {
			return errors.New("usage: sign KEYFILE VERSION FILE")
		}
		private, err := load(args[1])
		if err != nil {
			return err
		}
		version := args[2]
		if !update.Release(version) {
			return fmt.Errorf("%q isn't a release version (like 0.3.0)", version)
		}
		f, err := os.Open(args[3])
		if err != nil {
			return err
		}
		defer f.Close()
		sum := sha256.New()
		size, err := io.Copy(sum, f)
		if err != nil {
			return err
		}
		sha := hex.EncodeToString(sum.Sum(nil))
		sig := ed25519.Sign(private, update.Message(version, sha))
		return json.NewEncoder(os.Stdout).Encode(map[string]any{
			"version": version, "sha256": sha, "size": size, "signature": base64.StdEncoding.EncodeToString(sig)})
	default:
		return fmt.Errorf("unknown command %q", args[0])
	}
	return nil
}

func load(path string) (ed25519.PrivateKey, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	seed, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(b)))
	if err != nil || len(seed) != ed25519.SeedSize {
		return nil, fmt.Errorf("%s isn't a release key", path)
	}
	return ed25519.NewKeyFromSeed(seed), nil
}
