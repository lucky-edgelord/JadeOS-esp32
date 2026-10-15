# Jade's Python, frozen into the firmware (shared by the ESP32 board and the unix test build).
freeze(".", "_boot.py")
package("jade", base_path=".")
package("microdot", base_path="lib")
