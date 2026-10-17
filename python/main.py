# JadeOS on stock MicroPython: runs at every boot. `touch /jade/SAFE` (from the REPL: open("/jade/SAFE","w").close())
# to boot to a bare REPL, or press Ctrl-C within the first seconds.
import jade.app
jade.app.run()
