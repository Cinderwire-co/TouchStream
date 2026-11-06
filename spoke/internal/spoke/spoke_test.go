package spoke

import (
	"testing"
	"time"

	"github.com/Cinderwire-co/TouchStream/spoke/internal/config"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/direct"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/hub"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/recorder"
	"github.com/Cinderwire-co/TouchStream/spoke/internal/uplink"
)

func paired(t *testing.T) *Spoke {
	dir := t.TempDir()
	store, _ := config.Open(dir)
	_ = store.Update(func(c *config.Config) { c.HubURL, c.SpokeID, c.HubToken = "http://hub", "id", "t" })
	rec := &recorder.Recorder{Dir: t.TempDir()}
	// A link that's never run has never heard from the hub: it's out of reach
	return &Spoke{Store: store, Uplink: uplink.New(), Recorder: rec, Direct: direct.New(), Data: dir,
		Link: &hub.Link{Store: store}}
}

func TestARecordOnlyBroadcastIsRecordedWithoutTheHub(t *testing.T) {
	s := paired(t)
	now := time.Now()
	end := float64(now.Add(time.Hour).Unix())
	_ = direct.Save(s.Data, direct.Plan{Schedule: []direct.Event{{ID: 7, Title: "Sunday", StartAt: float64(now.Unix()), EndAt: &end}}})
	s.carryOn(now)
	if !s.Recorder.Recording() || s.mode != "live" {
		t.Fatalf("due with no channels and no hub: recording %v, mode %q", s.Recorder.Recording(), s.mode)
	}
	if s.Direct.Active() {
		t.Fatal("streamed with nowhere to stream to")
	}
	s.carryOn(now.Add(2 * time.Hour)) // past its end
	if s.Recorder.Recording() || s.mode != "off" {
		t.Fatalf("after its end: recording %v, mode %q", s.Recorder.Recording(), s.mode)
	}
}

func TestADueBroadcastGoesStraightToYouTube(t *testing.T) {
	s := paired(t)
	now := time.Now()
	_ = direct.Save(s.Data, direct.Plan{Schedule: []direct.Event{{ID: 8, Title: "Wednesday", StartAt: float64(now.Unix()),
		Targets: []direct.Target{{URL: "rtmps://a.rtmps.youtube.com/live2", Key: "k"}}}}})
	s.carryOn(now)
	if !s.Direct.Active() || !s.Recorder.Recording() {
		t.Fatalf("direct %v, recording %v", s.Direct.Active(), s.Recorder.Recording())
	}
}
