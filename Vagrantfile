# Vagrantfile for the 5G Kubernetes Testbed
#
# DEPLOYMENT MODES:
#   vagrant up                       - Deploy core + observability + dashboard, DEFAULT
#   DEPLOY_MODE=full vagrant up      - Deploy everything including UERANSIM
# PHYSICAL RAN (optional, disabled by default):
#   PHYSICAL_RAN_ENABLED=true PHYSICAL_RAN_BRIDGE=<host_nic> vagrant up
#
# After deployment, you can manually run phase 6 (UERANSIM) from ansible VM:
#   vagrant ssh ansible
#   cd ~/ansible-ro && ansible-playbook phases/06-ueransim-mec/playbook.yml -i inventory.ini
#
Vagrant.configure("2") do |config|
  config.ssh.insert_key = true
  config.vm.box_check_update = false
  
  # Deployment mode: "core_only" (default) or "full"
  deploy_mode = ENV['DEPLOY_MODE'] || 'core_only'
  physical_ran_enabled = (ENV['PHYSICAL_RAN_ENABLED'] || 'false').downcase == 'true'
  physical_ran_bridge = ENV['PHYSICAL_RAN_BRIDGE']

  nodes = {
    "master"  => { cpu: 4, mem: 4096, ip: "192.168.56.10", box: "ubuntu/jammy64" },
    "worker"  => { cpu: 8, mem: 8192, ip: "192.168.56.11", box: "ubuntu/jammy64" },
    "edge"    => { cpu: 4, mem: 4096, ip: "192.168.56.12", box: "ubuntu/jammy64" },
    "ansible" => { cpu: 2, mem: 1024, ip: "192.168.56.13", box: "ubuntu/jammy64" }
  }

  # Secondary network for physical RAN connection (worker only)
  # Disabled by default to avoid interactive bridge selection prompts.
  # NOTE: worker gets .1 (bridge role), AMF pod gets .150 via macvlan NAD.
  ran_network = {}
  if physical_ran_enabled
    if physical_ran_bridge.nil? || physical_ran_bridge.empty?
      puts "[WARN] PHYSICAL_RAN_ENABLED=true but PHYSICAL_RAN_BRIDGE is not set."
      puts "[WARN] Physical RAN bridge NIC will be skipped to avoid interactive prompts."
    else
      ran_network["worker"] = {
        ip: "192.168.6.1",
        netmask: "255.255.255.0",
        bridge: physical_ran_bridge || "enx00e04c6817b7"
      }
    end
  end

  nodes.each do |name, spec|
    config.vm.define name, primary: (name == "ansible") do |m|
      m.vm.hostname = name
      m.vm.network "private_network", ip: spec[:ip]
      m.vm.box = spec[:box]
      
      # Add bridged RAN network interface for worker (for physical femtocell)
      if ran_network.key?(name)
        m.vm.network "public_network", 
          ip: ran_network[name][:ip],
          netmask: ran_network[name][:netmask],
          bridge: ran_network[name][:bridge],
          use_dhcp_assigned_default_route: false
      end

      m.vm.provider "virtualbox" do |vb|
        vb.cpus   = spec[:cpu]
        vb.memory = spec[:mem]
        vb.name = "#{name}-5g-k8s-testbed"
        
        # Enable promiscuous mode on RAN interface for OVS bridging
        if ran_network.key?(name)
          vb.customize ["modifyvm", :id, "--nicpromisc3", "allow-all"]
        end
      end

      # Robust VM time sync with host (VirtualBox Guest Additions)
      # Use the VM UUID from Vagrant (`machine.id`) instead of guessing the VM name.
      m.trigger.after [:up, :resume, :reload] do |t|
        t.name = "Enable VM time sync (#{name})"
        t.ruby do |_env, machine|
          # This remains stable even if the displayed VM name changes.
          system("VBoxManage guestproperty set \"#{machine.id}\" \"/VirtualBox/GuestAdd/VBoxService/--timesync-interval\" \"10000\" >/dev/null 2>&1 || true")
        end
      end

      # Enable promiscuous mode on the RAN NIC so OVS bridging works.
      # Worker interface name (e.g. enp0s9) is auto-detected by IP: Vagrant assigns
      # 192.168.6.1 to the bridged NIC, so we grep for that. Same logic as OVS setup.
      if ran_network.key?(name)
        ran_ip = ran_network[name][:ip]
        m.trigger.after [:up, :resume, :reload] do |t|
          t.name = "Enable promiscuous mode on #{name} RAN interface"
          t.run = {
            inline: "vagrant ssh #{name} -c 'RAN_IF=$(ip -o addr show | grep #{ran_ip} | awk \"{print \\$2}\" | head -1) && [ -n \"$RAN_IF\" ] && sudo ip link set $RAN_IF promisc on && echo \"Promiscuous mode enabled on $RAN_IF\" || echo \"RAN interface not found for #{ran_ip}\"'"
          }
        end
      end

      # Persist host NIC used for RAN bridge so the dashboard can verify it (no trust required).
      # File is synced to ansible VM /vagrant and read by ran_service.
      if name == "worker"
        m.trigger.after [:up, :resume, :reload] do |t|
          t.name = "Persist physical RAN bridge for dashboard"
          t.ruby do |_env, _machine|
            path = File.join(File.dirname(__FILE__), ".physical_ran_bridge_applied")
            val = ran_network.key?("worker") && !physical_ran_bridge.to_s.strip.empty? ? physical_ran_bridge.strip : ""
            File.write(path, val)
          end
        end
      end

      # Enable outbound Internet access for the N6 Data Network (10.207.0.0/24) via NAT on the worker.
      # We apply it on every boot/reload because iptables rules are not persistent by default.
      if name == "worker"
        m.vm.provision "shell", run: "always", privileged: true, inline: <<-SHELL

          # Remove AMF static IP from CNI networks to avoid route conflicts.
          # This runs before k3s agent restart to avoid route conflicts.
          sudo rm -f /var/lib/cni/networks/n1-net/10.201.0.100 /var/lib/cni/networks/n2-net/10.202.0.100

          echo "[N6 Routing] Enabling IP forwarding..."
          sysctl -w net.ipv4.ip_forward=1 >/dev/null

          # Make sysctl persistent across Vagrant reboots
          grep -q "^net.ipv4.ip_forward=1$" /etc/sysctl.conf || echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf

          echo "[N6 Routing] Configuring outbound NAT policy for 10.207.0.0/24..."
          OUT_IF="$(ip route show default 2>/dev/null | awk '/default/ {print $5; exit}')"
          if [ -z "$OUT_IF" ]; then
            echo "[N6 Routing] WARN: default interface not found; skipping NAT"
            exit 0
          fi
          IPT="/usr/sbin/iptables-nft"
          [ -x "$IPT" ] || IPT="$(command -v iptables)"
          IPT_LEGACY="/usr/sbin/iptables-legacy"
          echo "[N6 Routing] Using backend: $IPT"

          # 1) Cleanup: remove previous N6 rules to avoid duplicates/order drift.
          echo "[N6 Routing] Cleaning old N6 NAT rules..."
          "$IPT" -t nat -S POSTROUTING | awk 'index($0, "10.207.0.0/24") {sub(/^-A/, "-D"); print}' | while read -r rule; do
            "$IPT" -t nat $rule || true
          done
          # Also clean legacy leftovers so diagnostics stay consistent.
          if [ -x "$IPT_LEGACY" ]; then
            "$IPT_LEGACY" -t nat -S POSTROUTING 2>/dev/null | awk 'index($0, "10.207.0.0/24") {sub(/^-A/, "-D"); print}' | while read -r rule; do
              "$IPT_LEGACY" -t nat $rule || true
            done
          fi

          # 2) Private destinations bypass NAT (RETURN from POSTROUTING in NAT table).
          echo "[N6 Routing] Adding private-network bypass rules..."
          for private_net in 10.0.0.0/8 172.16.0.0/12 192.168.56.0/24 192.168.6.0/24; do
            "$IPT" -t nat -A POSTROUTING -s 10.207.0.0/24 -d "$private_net" -j RETURN
          done

          # 3) Catch-all: public egress from N6 is masqueraded on the default outbound interface.
          echo "[N6 Routing] Enabling outbound MASQUERADE via $OUT_IF..."
          "$IPT" -t nat -A POSTROUTING -s 10.207.0.0/24 -o "$OUT_IF" -j MASQUERADE
        SHELL
      end

      if name != "ansible"
        m.vm.synced_folder ".", "/vagrant", disabled: true
      else
        m.vm.synced_folder ".", "/vagrant", disabled: false
        m.vm.synced_folder "ansible/", "/home/vagrant/ansible-ro",
          create: true,
          mount_options: ["ro"]
      end
    end
  end

  # Provisioning for the "ansible" VM
  config.vm.define "ansible", primary: true do |ansible|
    # --- Root block: system packages
    ansible.vm.provision "shell", privileged: true, inline: <<-SHELL
      set -euo pipefail
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -y
      apt-get install -y python3-pip git
    SHELL

    # --- User vagrant block: ansible + collections + ssh setup
    ansible.vm.provision "shell", privileged: false, inline: <<-'SHELL'
      set -euo pipefail
      export PATH="$HOME/.local/bin:$PATH"

      # Ansible for the vagrant user
      python3 -m pip install --user 'ansible==9.7.0'
      # Client Python for Kubernetes used by kubernetes.core
      python3 -m pip install --user 'kubernetes>=29.0.0'

      # Collections from requirements.yml if present
      if [ -f /home/vagrant/ansible-ro/requirements.yml ]; then
        ansible-galaxy collection install -r /home/vagrant/ansible-ro/requirements.yml
      else
        echo "[INFO] /home/vagrant/ansible-ro/requirements.yml not found: skipping collections installation"
      fi

      # SSH keys from private_key Vagrant mounted in /vagrant/.vagrant
      mkdir -p /home/vagrant/.ssh
      chmod 700 /home/vagrant/.ssh
      for vm in master worker edge; do
        key_path="/vagrant/.vagrant/machines/$vm/virtualbox/private_key"
        if [ -f "$key_path" ]; then
          cp "$key_path" "/home/vagrant/.ssh/${vm}_key"
          chmod 600 "/home/vagrant/.ssh/${vm}_key"
          echo "Copied SSH key for $vm to /home/vagrant/.ssh/${vm}_key"
        else
          echo "[WARN] Key not found for $vm (path: $key_path)"
        fi
      done

      # Use ssh_config already versioned in the repo
      cp /home/vagrant/ansible-ro/ssh_config /home/vagrant/.ssh/config
      chmod 600 /home/vagrant/.ssh/config

      # Workspace Ansible (writable)
      mkdir -p /home/vagrant/ansible-work/{logs,cache,tmp,retry}
      cp /home/vagrant/ansible-ro/ansible.cfg /home/vagrant/ansible-work/ansible.cfg
      chmod 644 /home/vagrant/ansible-work/ansible.cfg

      # Add PATH to .bashrc for interactive sessions
      if ! grep -q 'export PATH=.*.local/bin' ~/.bashrc; then
        echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.bashrc
      fi

      # Ensure interactive sessions use writable Ansible config.
      # This avoids ansible.cfg auto-discovery issues in world-writable synced folders.
      if ! grep -q 'export ANSIBLE_CONFIG=/home/vagrant/ansible-work/ansible.cfg' ~/.bashrc; then
        echo 'export ANSIBLE_CONFIG=/home/vagrant/ansible-work/ansible.cfg' >> ~/.bashrc
      fi
    SHELL

    # --- Final run (always): wait for SSH + timed playbook execution

    ansible.vm.provision "shell", run: "always", privileged: false, inline: <<-'SHELL'
      set -euo pipefail
      export PATH="$HOME/.local/bin:$PATH"
      export ANSIBLE_CONFIG=/home/vagrant/ansible-work/ansible.cfg
      deploy_mode="${DEPLOY_MODE:-core_only}"
      physical_ran_enabled="${PHYSICAL_RAN_ENABLED:-false}"
      if [ "$physical_ran_enabled" = "true" ]; then
        ran_extra="-e physical_ran_enabled=true"
      else
        ran_extra="-e physical_ran_enabled=false"
      fi

      t0=$(date +%s)

      echo "=== Waiting for SSH on VMs ==="
      wait_ssh() {
        local host="$1" tries=15
        for i in $(seq 1 $tries); do
          if ssh -o ConnectTimeout=10 -o BatchMode=yes "$host" 'echo OK' >/dev/null 2>&1; then
            echo "$host reachable (attempt $i)"
            return 0
          fi
          echo "Attempt $i: $host not reachable, retrying..."
          sleep 10
        done
        echo "ERROR: $host not reachable after $tries attempts"
        return 1
      }

      for vm in master worker edge; do wait_ssh "$vm"; done

      echo "=== Running phased playbook (timed) ==="
      echo "DEPLOY_MODE: ${deploy_mode}"
      echo "PHYSICAL_RAN_ENABLED: ${physical_ran_enabled}"
      pb_t0=$(date +%s)
      
      if [ "${deploy_mode}" = "full" ]; then
        echo "🚀 Full deployment mode: including UERANSIM (phase 6)"
        ansible-playbook /home/vagrant/ansible-ro/phases/00-main-playbook.yml ${ran_extra}
      else
        echo "🔧 Core-only mode (default): deploying phases 1-5 + phase 7 + phase 8"
        echo "   To add UERANSIM later, run from ansible VM:"
        echo "   cd ~/ansible-ro && ansible-playbook phases/06-ueransim-mec/playbook.yml -i inventory.ini"
        ansible-playbook /home/vagrant/ansible-ro/phases/00-main-playbook.yml ${ran_extra} --skip-tags phase6,ueransim,mec
      fi
      pb_t1=$(date +%s)

      t1=$(date +%s)

      echo "=== Timing summary ==="
      echo "Playbook runtime: $((pb_t1 - pb_t0)) seconds"
      echo "Provisioning (this script): $((t1 - t0)) seconds"

      # Optional: store timings for later use
      mkdir -p /home/vagrant/ansible-work/logs
      {
        echo "playbook_seconds=$((pb_t1 - pb_t0))"
        echo "provision_seconds=$((t1 - t0))"
      } > /home/vagrant/ansible-work/logs/provision.timings
    SHELL

  end
end
