{
  description = "pdd-repository — Nix-native Protocol-Driven Development registry, deployable to the staging k3s host";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
    nix2container = {
      url = "github:nlewo/nix2container";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, nix2container }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      nix2containerPkgs = nix2container.packages.${system};

      imageName = "ghcr.io/tactile-taco/pdd-repository";
      # The port the service listens on inside the container.
      # Must match containerPort/Service/Ingress in deploy/k8s.yaml.
      port = 8080;

      # Python runtime with the pdd toolchain (CLI, validators, tests).
      pythonEnv = pkgs.python3.withPackages (ps: [
        ps.pytest
        ps.hypothesis
        ps.jsonschema
        ps.pyyaml
        ps.psycopg
      ]);

      # Assemble the repo content (tracked files) into /opt/pdd inside the image.
      repo = pkgs.runCommand "pdd-repository-content" { } ''
        mkdir -p $out/opt/pdd
        cp -r ${./src}/.           $out/opt/pdd/src/
        cp -r ${./scripts}/.       $out/opt/pdd/scripts/
        cp -r ${./validators}/.    $out/opt/pdd/validators/
        cp -r ${./pdd-bundles}/.   $out/opt/pdd/pdd-bundles/
        cp -r ${./implementations}/. $out/opt/pdd/implementations/
        cp -r ${./evidence}/.      $out/opt/pdd/evidence/
        cp -r ${./.reasonix}/.     $out/opt/pdd/.reasonix/
        cp ${./Makefile}           $out/opt/pdd/Makefile
        cp ${./README.md}          $out/opt/pdd/README.md
      '';

      imageRoot = pkgs.buildEnv {
        name = "pdd-image-root";
        paths = [ repo pythonEnv ];
        pathsToLink = [ "/opt/pdd" "/bin" "/lib" ];
      };
    in
    {
      packages.${system} = {
        # The service entry point (stdlib HTTP service; see src/server.py).
        default = pkgs.writeShellApplication {
          name = "pdd-server";
          runtimeInputs = [ pythonEnv ];
          text = ''
            exec ${pythonEnv}/bin/python3 /opt/pdd/src/server.py
          '';
        };

        # OCI image. Build: nix build .#image
        # Push:  see deploy/push.sh (no Docker daemon required)
        image = nix2containerPkgs.nix2container.buildImage {
          name = imageName;
          config = {
            Cmd = [ "/opt/pdd/src/server.py" ];
            Entrypoint = [ "${pythonEnv}/bin/python3" ];
            Env = [ "PORT=${toString port}" "PYTHONUNBUFFERED=1" ];
            WorkingDir = "/opt/pdd";
            ExposedPorts = { "${toString port}/tcp" = { }; };
          };
        };

        # The full runtime closure as a plain store path (for debugging/extraction).
        runtime = imageRoot;
      };

      # Definition of done for agents: nix flake check must pass.
      checks.${system} = {
        smoke = pkgs.runCommand "pdd-smoke" { nativeBuildInputs = [ pythonEnv ]; } ''
          python3 -c "
import ast, sys
ast.parse(open('${./src/server.py}').read())
print('server.py parses OK')
"
          touch $out
        '';
        lint = pkgs.runCommand "pdd-bundle-lint" { nativeBuildInputs = [ pythonEnv ]; } ''
          cp -r ${./pdd-bundles} pdd-bundles
          cp -r ${./.reasonix}/skills/pdd-protocol-author/scripts/check_bundle.py check_bundle.py
          # Catalog mode: per-bundle grammar (incl. namespace/tags) + cross-bundle
          # (namespace, name) uniqueness (S-004).
          python3 check_bundle.py --catalog pdd-bundles
          touch $out
        '';
      };

      devShells.${system}.default = pkgs.mkShell {
        packages = with pkgs; [ pythonEnv skopeo ];
      };
    };
}
