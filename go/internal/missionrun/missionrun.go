// Package missionrun wires orch's mission spine to the agent loop.
//
// It exists for one reason: import direction. Python's run_mission does
// `from agent_loop import run_agent_loop` INSIDE the function body,
// which is the idiom for "this would be a cycle at module scope". Go has
// no lazy import, so the edge lives here instead of in orch — `orch`
// stays free of `loop`, and the two can keep growing toward each other
// without either one having to know about the other.
//
// Everything in this package is wiring. If a behaviour matters, it
// belongs in orch or in loop, not here.
package missionrun

import (
	"context"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/loop"
	"github.com/slycrel/maro-orchestration/go/internal/orch"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Opts are the loop settings a mission's features inherit.
type Opts struct {
	Model    string
	MaxSteps int
	// Exec turns on the tool-bearing executor lane for feature work.
	Exec bool
}

// FeatureRunner returns the orch.RunFeatureFn that run_agent_loop is on
// the Python side.
//
// NAMED DIVERGENCE — two of run_agent_loop's keyword arguments have no
// home in loop.Opts and are dropped here rather than faked:
//
//	project=              the loop resolves its OWN project slug from the
//	                      feature title, so a mission's features can land
//	                      in per-feature project dirs where Python puts
//	                      them all under the mission's project.
//	ancestry_context_extra=  the injected hook context, boot-protocol
//	                      block and sprint-contract criteria. All three
//	                      producers are unported, so today this is always
//	                      empty and the drop costs nothing — but it will
//	                      the moment any of them lands.
//
// Both are in PORT.md. They are dropped VISIBLY (the request carries
// them; this function ignores them) rather than removed from the seam,
// because a seam that never carried them could not show the gap.
func FeatureRunner(a llm.Adapter, rec *record.Recorder, o Opts) orch.RunFeatureFn {
	return func(ctx context.Context, req orch.FeatureRequest) (orch.FeatureOutcome, error) {
		res, err := loop.Run(ctx, a, rec, loop.Opts{
			Goal:     req.Title,
			Model:    o.Model,
			MaxSteps: o.MaxSteps,
			DryRun:   req.DryRun,
			Exec:     o.Exec,
		})
		if err != nil {
			return orch.FeatureOutcome{}, err
		}
		done := 0
		for _, s := range res.Steps {
			if s.Status == "done" {
				done++
			}
		}
		return orch.FeatureOutcome{
			LoopID:     res.LoopID,
			Status:     res.Status,
			StepsDone:  done,
			StepsTotal: len(res.Steps),
		}, nil
	}
}

// SlugResolver is loop_artifacts.resolve_project_slug bound to a
// workspace. The warning the loop's own resolver returns is dropped:
// run_mission has no warning channel, and Python's call site
// (`project = resolve_project_slug(goal)`) has none either.
func SlugResolver(ws string) func(goal string) string {
	return func(goal string) string {
		slug, _ := loop.ResolveProjectSlug(orch.ProjectsRoot(ws), goal)
		return slug
	}
}
