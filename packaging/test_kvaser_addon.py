import json, pathlib, unittest
R=pathlib.Path(__file__).resolve().parents[1]
class Tests(unittest.TestCase):
 def test_guards_and_scope(self):
  s=(R/'packaging/kvaser-addon/install.sh').read_text(); lock=json.loads((R/'packaging/kvaser-addon/artifacts.lock.json').read_text())
  for x in ['6.18.50+rpt-rpi-v8','1:6.18.50-1+rpt1','0bfd','0120','bitrate 1000000','kvaser_usb']: self.assertIn(x,s)
  self.assertNotIn('CANlib', json.dumps(lock)); self.assertEqual([a['package'] for a in lock['artifacts']],['Kvaser SocketCAN driver','make'])
if __name__=='__main__': unittest.main()
