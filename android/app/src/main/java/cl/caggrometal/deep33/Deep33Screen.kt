package cl.caggrometal.deep33

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.FilterChip
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

enum class LinkState { IDLE, SYNCING, ONLINE, ERROR }

@Composable
fun Deep33Screen(
    personality: Personality,
    linkState: LinkState,
    viseme: Float = 0f,
    onPersonalitySelected: (Personality) -> Unit = {}
) {
    Deep33Theme(personality) {
        Surface(
            modifier = Modifier.fillMaxSize(),
            color = Color(0xFF080A0F)
        ) {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(horizontal = 18.dp, vertical = 14.dp),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Text(
                    text = "DEEP33",
                    color = Color.White,
                    fontSize = 25.sp,
                    fontWeight = FontWeight.SemiBold
                )

                Spacer(Modifier.height(4.dp))

                Text(
                    text = personality.key,
                    color = Color(personality.accent),
                    fontSize = 13.sp,
                    fontWeight = FontWeight.Medium
                )

                if (linkState == LinkState.SYNCING) {
                    Spacer(Modifier.height(8.dp))
                    LinearProgressIndicator(
                        modifier = Modifier.fillMaxWidth(),
                        color = Color(personality.accent),
                        trackColor = Color(0xFF202631)
                    )
                }

                Spacer(Modifier.height(24.dp))

                VisemeAvatar(
                    personality = personality,
                    viseme = viseme,
                    modifier = Modifier.size(184.dp)
                )

                Spacer(Modifier.height(18.dp))

                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(24.dp),
                    color = Color(0xFF11151D)
                ) {
                    Column(Modifier.padding(16.dp)) {
                        Text(
                            text = "PERSONALIDAD",
                            color = Color(0xFFAAB2C0),
                            fontSize = 12.sp
                        )
                        Spacer(Modifier.height(10.dp))
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            Personality.entries.forEach { option ->
                                FilterChip(
                                    selected = option == personality,
                                    onClick = { onPersonalitySelected(option) },
                                    label = { Text(option.key) }
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}
