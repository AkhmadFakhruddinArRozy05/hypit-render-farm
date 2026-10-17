#!/usr/bin/env python3
"""
Scaffolds a new Hypit video project inside hypit-render-farm/projects/<name>
Pre-configured for 16 GB RAM GitHub Actions cloud rendering, WhisperX ID alignment,
and universal asset ingestion.
"""

import argparse
import json
import os
import sys
from pathlib import Path

RECIPES_SVS_TEMPLATE = '''<?svml using="@hypit/markup@1"?>
<style-sheet>
  <style name="caption.primary">
    font-size: 52px;
    font-weight: 800;
    color: #FFFFFF;
    text-shadow: 0 4px 12px rgba(0, 0, 0, 0.85);
    text-transform: uppercase;
    text-align: center;
    background: rgba(0, 0, 0, 0.4);
    padding: 12px 24px;
    border-radius: 16px;
  </style>

  <style name="card.hook">
    background: #FF0050;
    color: #FFFFFF;
    font-size: 38px;
    font-weight: 800;
    text-align: center;
    border-radius: 20px;
    padding: 18px 28px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
  </style>

  <style name="film.vertical">
    background-color: #000000;
  </style>
</style-sheet>
'''

PROJECT_SVML_TEMPLATE = '''<?svml using="@hypit/markup@1"?>
<svml>
  <import from="@hypit/script@1"/>
  <import as="asset" from="@hypit/media@1"/>
  <import as="media" from="@hypit/media@1"/>
  <import as="mediaop" from="@hypit/media-operations@1"/>
  <import as="time" from="@hypit/timeline-author@1"/>
  <import as="space" from="@hypit/spatial@1"/>
  <import as="visual" from="@hypit/visual-track@1"/>
  <import as="audio" from="@hypit/audio-track@1"/>
  <import as="caption" from="@hypit/caption@1"/>
  <import as="caption-fine" from="@hypit/caption-fine@1"/>
  <import as="fonts" from="@hypit/fontsource@1"/>
  <import as="film" from="@hypit/film@1"/>
  <import as="html" from="@hypit/html-video@1"/>
  <import as="recipes" source="./recipes.svs"/>

  <script id="story">
    <hook>
      <HOST>Halo semuanya! Ini template proyek Hypit Cloud Farm.</HOST>
    </hook>
  </script>

  <!-- Canvas, Clock & Timeline (Vertical 9:16 Full HD) -->
  <space:Canvas id="canvas" width="1080" height="1920"/>
  <space:Frame id="full-frame" within={canvas.bounds} left="0%" top="0%" right="100%" bottom="100%"/>
  <time:Clock id="clock" frame-rate="30"/>

  <time:Timeline id="main-timeline" clock={clock} end="intro-window.end">
    <time:Window id="intro-window" from="start" for="150f"/>
  </time:Timeline>

  <!-- Typography -->
  <fonts:Face id="font-inter-bold" package="@fontsource-variable/inter" weight="700" style="normal"/>
  <asset:FontStack id="ui-font" primary={font-inter-bold}/>

  <!-- Master Composition -->
  <film:Film id="main" canvas={canvas.canvas} timeline={main-timeline.timeline}
    appearance={recipes.film.vertical}>
  </film:Film>

  <!-- Output Video Target (Exported via hypit get final.video) -->
  <html:Video id="final" composition={main.composition} timeline={main-timeline.timeline}/>
</svml>
'''

BUILD_SVRUN_TEMPLATE = '''<?svml using="@hypit/markup/run@1"?>
<svrun version="1">
  <author source="./project.svml"/>
  <target output="final.video"/>
</svrun>
'''

def main():
    parser = argparse.ArgumentParser(description="Create a new Hypit project in the Cloud Render Farm")
    parser.add_argument("name", help="Slug name of project folder (e.g. product-launch-video)")
    parser.add_argument("--title", default="", help="Human readable project title")
    args = parser.parse_args()

    slug = args.name.strip().lower().replace(" ", "-")
    title = args.title or slug.replace("-", " ").title()

    farm_root = Path(__file__).resolve().parent.parent
    project_dir = farm_root / "projects" / slug

    if project_dir.exists():
        print(f"[ERROR] Project directory already exists at: {project_dir}")
        sys.exit(1)

    project_dir.mkdir(parents=True)
    (project_dir / "assets").mkdir()
    (project_dir / "assets" / ".gitkeep").touch()

    # 1. package.json
    package_json = {
        "name": slug,
        "version": "0.1.0",
        "description": title,
        "private": True,
        "type": "module",
        "hypit": {
            "project": True
        },
        "dependencies": {
            "@fontsource-variable/inter": "^5.2.8",
            "@fontsource-variable/montserrat": "^5.2.8"
        }
    }
    with open(project_dir / "package.json", "w") as f:
        json.dump(package_json, f, indent=2)

    # 2. hypit.runtime.json
    runtime_json = {
        "format": "hypit.runtime-local@1",
        "dataRoot": ".hypit/runtimes/local",
        "credentials": {
            "local": {
                "use": "@hypit/credential-store-local"
            }
        },
        "endpoints": {
            "media.local": {
                "use": "@hypit/media-local",
                "config": {
                    "defaultConcurrency": 2
                }
            },
            "whisperx.local": {
                "use": "@hypit/provider-whisperx-local",
                "config": {
                    "baseUrl": "http://127.0.0.1:8765",
                    "expectedModel": "small",
                    "expectedDevice": "cpu",
                    "expectedCompute": "int8",
                    "expectedBatchSize": 8,
                    "alignmentLanguages": ["id"],
                    "defaultConcurrency": 1
                }
            },
            "html.local": {
                "use": "@hypit/provider-html-local",
                "config": {
                    "workers": 2,
                    "quality": "standard",
                    "defaultConcurrency": 1,
                    "processTimeoutMs": 7200000
                }
            }
        },
        "bindings": {
            "@hypit/whisperx@1#whisperx-alignment": "whisperx.local"
        }
    }
    with open(project_dir / "hypit.runtime.json", "w") as f:
        json.dump(runtime_json, f, indent=2)

    # 3. source_manifest.json
    manifest_json = {
        "project_name": slug,
        "title": title,
        "run_file": "build.svrun",
        "output_name": f"{slug}.mp4",
        "sources": []
    }
    with open(project_dir / "source_manifest.json", "w") as f:
        json.dump(manifest_json, f, indent=2)

    # 4. recipes.svs
    with open(project_dir / "recipes.svs", "w") as f:
        f.write(RECIPES_SVS_TEMPLATE)

    # 5. project.svml
    with open(project_dir / "project.svml", "w") as f:
        f.write(PROJECT_SVML_TEMPLATE)

    # 6. build.svrun
    with open(project_dir / "build.svrun", "w") as f:
        f.write(BUILD_SVRUN_TEMPLATE)

    print(f"\n=======================================================")
    print(f"✨ NEW HYPIT PROJECT CREATED: {title}")
    print(f"=======================================================")
    print(f"📁 Location: {project_dir}")
    print(f"📄 Files created:")
    print(f"   • package.json")
    print(f"   • hypit.runtime.json (WhisperX ID enabled)")
    print(f"   • source_manifest.json (Cloud farm ingestion manifest)")
    print(f"   • recipes.svs")
    print(f"   • project.svml")
    print(f"   • build.svrun")
    print(f"   • assets/ (.gitkeep)")
    print(f"\n🚀 Next Steps:")
    print(f"   1. Edit {project_dir / 'project.svml'} and add assets to assets/ or source_manifest.json")
    print(f"   2. Render via Cloud Farm (16 GB RAM GitHub Actions):")
    print(f"      python3 scripts/render_cloud.py --project {slug}")
    print(f"=======================================================\n")

if __name__ == "__main__":
    main()
