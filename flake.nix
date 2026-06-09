{
  description = "EchoBooks — TUI reading catalog + optional self-hosted sync server";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
      ...
    }:
    let
      inherit (nixpkgs) lib;
      forAllSystems = lib.genAttrs lib.systems.flakeExposed;

      # Load the uv workspace (reads pyproject.toml + uv.lock).
      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };

      # Prefer prebuilt wheels: this is what lets the C-extension deps
      # (psycopg, asyncpg, argon2-cffi) come down ready-built instead of
      # compiling from source — and removes the host's nix-ld dependency.
      overlay = workspace.mkPyprojectOverlay {
        sourcePreference = "wheel";
      };

      # Pin Python 3.12 (pyproject requires >=3.12; the server box pins 3.12).
      pythonSets = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python312;
        in
        (pkgs.callPackage pyproject-nix.build.packages { inherit python; }).overrideScope (
          lib.composeManyExtensions [
            pyproject-build-systems.overlays.wheel
            overlay
          ]
        )
      );

      # The env including the `server` optional-dependency group — this is what
      # the server runs. `deps.optionals` resolves core deps + all extras
      # (only `server` exists), so it carries fastapi/uvicorn/psycopg/etc.
      mkServerEnv =
        system: pythonSets.${system}.mkVirtualEnv "echobooks-env" workspace.deps.optionals;
    in
    {
      packages = forAllSystems (system: {
        # The full app: TUI client + server, with console scripts on PATH
        # (`echobooks`, `echobooks-server`).
        default = mkServerEnv system;
        echobooks = mkServerEnv system;
      });

      # `nix run .#server` launches the API directly.
      apps = forAllSystems (system: {
        server = {
          type = "app";
          program = "${mkServerEnv system}/bin/echobooks-server";
        };
        default = self.apps.${system}.server;
      });

      # NixOS module: systemd service + Postgres provisioning.
      nixosModules.default = import ./nix/module.nix self;

      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          default = pkgs.mkShell {
            packages = [
              (mkServerEnv system)
              pkgs.uv
            ];
            env = {
              UV_NO_SYNC = "1";
              UV_PYTHON_DOWNLOADS = "never";
            };
            shellHook = ''
              unset PYTHONPATH
            '';
          };
        }
      );
    };
}
