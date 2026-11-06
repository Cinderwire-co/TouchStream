package direct

import (
	"strings"
	"testing"
	"time"
)

func TestDueFollowsTheSchedule(t *testing.T) {
	now := time.Unix(1_000_000, 0)
	end := float64(now.Unix() + 3600)
	off := false
	p := Plan{Schedule: []Event{
		{ID: 1, StartAt: float64(now.Unix() - 7200), EndAt: ptr(float64(now.Unix() - 3600))}, // over
		{ID: 2, StartAt: float64(now.Unix() + 5), EndAt: &end},                               // starts in 5 s: within the lead
		{ID: 3, StartAt: float64(now.Unix() - 60), AutoStart: &off},                          // started by hand only
	}}
	if e := p.Due(now); e == nil || e.ID != 2 {
		t.Fatalf("due: %+v", e)
	}
	if e := p.Due(now.Add(2 * time.Hour)); e != nil {
		t.Fatalf("past its end: %+v", e)
	}
	open := Plan{Schedule: []Event{{ID: 4, StartAt: float64(now.Unix())}}}
	if open.Due(now.Add(3*time.Hour)) == nil || open.Due(now.Add(5*time.Hour)) != nil {
		t.Fatal("a broadcast with no end should run for 4 hours")
	}
}

func TestTheStreamGoesToEveryTargetUntouched(t *testing.T) {
	args := strings.Join(Args([]Target{{URL: "rtmp://a.rtmp.youtube.com/live2/", Key: "abcd"}, {URL: "rtmps://b/app", Key: "k"}}), " ")
	for _, want := range []string{"-c copy", "-tag:v 7 -tag:a 10", "[f=flv:onfail=ignore:bsfs/a=aac_adtstoasc]rtmp://a.rtmp.youtube.com/live2/abcd|[f=flv:onfail=ignore:bsfs/a=aac_adtstoasc]rtmps://b/app/k"} {
		if !strings.Contains(args, want) {
			t.Fatalf("missing %q in %s", want, args)
		}
	}
}

func ptr(v float64) *float64 { return &v }
