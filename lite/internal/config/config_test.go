package config

import "testing"

func TestAnotherProcessesChangesAreSeen(t *testing.T) {
	dir := t.TempDir()
	running, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	if running.Get().DeviceName == "" {
		t.Fatal("a new spoke should be named after its host")
	}
	pairing, _ := Open(dir) // touchstream-lite pair, a separate process
	if err := pairing.Update(func(c *Config) { c.HubURL, c.SpokeID, c.HubToken = "http://hub", "abc", "t" }); err != nil {
		t.Fatal(err)
	}
	if !running.Get().Paired() {
		t.Fatal("the running spoke didn't see the pairing")
	}
}
