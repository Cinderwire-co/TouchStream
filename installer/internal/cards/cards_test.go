package cards

import "testing"

func disk(id string, d Disk) Disk { d.ID = id; d.Name = "x"; return d }

func TestOnlyCardsAreOffered(t *testing.T) {
	disks := []Disk{
		disk("ssd", Disk{Card: Card{Bus: "Apple Fabric", Size: 1 << 40}, Internal: true}),
		disk("system", Disk{Card: Card{Bus: "USB", Size: 64 << 30}, Removable: true, System: true}),
		disk("reader-empty", Disk{Card: Card{Bus: "USB", Size: 0}, Removable: true}),
		disk("backup-drive", Disk{Card: Card{Bus: "USB", Size: 2 << 40}, Removable: true}),
		disk("usb-fixed", Disk{Card: Card{Bus: "USB", Size: 128 << 30}}),
		disk("sata", Disk{Card: Card{Bus: "SATA", Size: 64 << 30}, Removable: true}),
		disk("image", Disk{Card: Card{Bus: "Disk Image", Size: 64 << 20, Virtual: true}, Removable: true}),
		disk("simulator", Disk{Card: Card{Bus: "Disk Image", Size: 19 << 30, Virtual: true, ReadOnly: true}, Removable: true}),
		disk("usb-reader", Disk{Card: Card{Bus: "USB", Size: 32 << 30}, Removable: true}),
		disk("built-in-slot", Disk{Card: Card{Bus: "Secure Digital", Size: 64 << 30}, Internal: true, Removable: true}),
		disk("windows-sd", Disk{Card: Card{Bus: "SD", Size: 64 << 30}, Removable: true}),
		// What an Apple-silicon Mac builds on its own SSD, and on an APFS card: never offered, even in developer mode
		disk("apfs-on-ssd", Disk{Card: Card{Bus: "Apple Fabric", Size: 5 << 30}, Internal: true, System: true, Synthesized: true}),
		disk("apfs-on-card", Disk{Card: Card{Bus: "Secure Digital", Size: 64 << 30}, Internal: true, Removable: true, Synthesized: true}),
	}
	ids := func(cs []Card) (out []string) {
		for _, c := range cs {
			out = append(out, c.ID)
		}
		return
	}
	got := ids(Offer(disks, false))
	want := []string{"usb-reader", "built-in-slot", "windows-sd"}
	if len(got) != len(want) || got[0] != want[0] || got[1] != want[1] || got[2] != want[2] {
		t.Fatalf("offered %v, want %v", got, want)
	}
	if dev := ids(Offer(disks, true)); len(dev) != 4 || dev[3] != "image" { // after the real cards, and never a locked image
		t.Fatalf("developer mode offered %v", dev)
	}
}

func TestASwappedCardIsntErased(t *testing.T) {
	chosen := Card{ID: "disk4", Name: "SDXC", Size: 32 << 30}
	if err := Same(chosen, []Card{chosen}); err != nil {
		t.Fatal(err)
	}
	if err := Same(chosen, []Card{{ID: "disk4", Name: "SDXC", Size: 64 << 30}}); err != ErrChanged {
		t.Fatalf("a different card: %v", err)
	}
	if err := Same(chosen, nil); err != ErrGone {
		t.Fatalf("taken out: %v", err)
	}
	locked := chosen
	locked.ReadOnly = true
	if err := Same(chosen, []Card{locked}); err != ErrReadOnly {
		t.Fatalf("locked: %v", err)
	}
}
