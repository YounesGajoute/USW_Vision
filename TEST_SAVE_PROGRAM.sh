#!/bin/bash

# Test saving a program via API to see exact error

echo "Testing program creation..."
echo ""

curl -X POST http://localhost:5000/api/programs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "UI Test Program",
    "config": {
      "triggerType": "internal",
      "triggerInterval": 1000,
      "triggerDelay": 0,
      "brightnessMode": "normal",
      "focusValue": 50,
      "masterImage": null,
      "tools": [],
      "outputs": {
        "OUT1": "Always ON",
        "OUT2": "OK",
        "OUT3": "NG",
        "OUT4": "Not Used",
        "OUT5": "Not Used",
        "OUT6": "Not Used",
        "OUT7": "Not Used",
        "OUT8": "Not Used"
      }
    }
  }' 2>&1 | python -m json.tool

echo ""
echo ""
echo "If you see an error above, share it so I can fix it."
echo "If you see 'Program created successfully', then it works!"
