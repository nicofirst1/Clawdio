Block C: the SAME event script (calm read/grep -> busy writes -> a hot
12-call Bash burst -> back to busy edits -> Stop), rendered 3 times with
only the event-density -> tap-density mapping changed:

  c1_v2_mapping        v2: rate = 2 + 38*a^1.3, 20 ms pacing floor, no
                       burst coalescing. Measured 14.1 drops/s, peaking at
                       26.5/s -- this is what the v2 listener heard and
                       described as 'too fast, losing control'.
  c2_v22_mapping       v2.2 as shipped: compressive map capped at 6/s,
                       150 ms floor, 250 ms burst coalescing. 0.73 drops/s,
                       peaking at 3.0/s.
  c3_v22_half_density  v2.2 at half density: half the rate, 300 ms floor,
                       500 ms coalescing. 0.37 drops/s, peaking at 1.5/s.

This is the 'flip-point probe': play c1/c2/c3 in randomized order (see
eval/README.md) and ask which feels controllable vs frantic during the busy
burst, to find where pacing tips from informative into overwhelming. c2 is
the shipped setting; c1 and c3 bracket it on either side.
