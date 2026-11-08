// Package hubapi is the installer's side of the hub's API: it chooses the dashboard password on a
// new hub (or signs in to one already set up), and registers each spoke before its card is
// written, so the box is paired from its first boot.
//
// Copyright (c) 2025 Will Reeves and TouchStream Contributors
// Licensed under the MIT License - see LICENSE file for details
package hubapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"strings"
	"time"
)

type Client struct {
	URL  string
	http *http.Client
}

func New(url string) *Client {
	jar, _ := cookiejar.New(nil) // the dashboard's session, once signed in
	return &Client{URL: strings.TrimRight(url, "/"), http: &http.Client{Jar: jar, Timeout: 15 * time.Second}}
}

// Error is the hub saying no, in its own words.
type Error struct {
	Status int
	Detail string
}

func (e *Error) Error() string { return e.Detail }

func (c *Client) call(ctx context.Context, method, path string, body, out any) error {
	data, _ := json.Marshal(body)
	req, err := http.NewRequestWithContext(ctx, method, c.URL+path, bytes.NewReader(data))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("can't reach the hub at %s", c.URL)
	}
	defer resp.Body.Close()
	reply, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode >= 300 {
		var e struct {
			Detail any `json:"detail"`
		}
		_ = json.Unmarshal(reply, &e)
		detail, ok := e.Detail.(string)
		if !ok {
			detail = fmt.Sprintf("the hub said %d", resp.StatusCode)
		}
		return &Error{resp.StatusCode, detail}
	}
	if out != nil {
		return json.Unmarshal(reply, out)
	}
	return nil
}

// Setup chooses the dashboard password on a new hub, and signs in with it.
func (c *Client) Setup(ctx context.Context, password string) error {
	return c.call(ctx, "POST", "/api/setup", map[string]string{"password": password}, nil)
}

// SignIn signs in to a hub already set up.
func (c *Client) SignIn(ctx context.Context, password string) error {
	return c.call(ctx, "POST", "/api/login", map[string]string{"password": password}, nil)
}

// Spoke is a spoke registered before its box is switched on: what goes on its card.
type Spoke struct {
	ID     string `json:"spoke_id"`
	Token  string `json:"token"`
	HubURL string `json:"hub_url"`
}

// MakeSpoke registers a spoke called name.
func (c *Client) MakeSpoke(ctx context.Context, name string) (Spoke, error) {
	var s Spoke
	err := c.call(ctx, "POST", "/api/spokes", map[string]string{"name": name}, &s)
	return s, err
}

// ForgetSpoke removes a spoke registered for a card that then didn't get written.
func (c *Client) ForgetSpoke(ctx context.Context, id string) error {
	return c.call(ctx, "DELETE", "/api/spokes/"+id, nil, nil)
}
