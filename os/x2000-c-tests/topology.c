/* os/x2000-c-build.py: libatopology parses a topology configuration, saves
 * it as text and builds the binary; both go to stdout. */
#include <alsa/asoundlib.h>
#include <alsa/topology.h>
#include <stdio.h>
#include <stdlib.h>

static const char conf[] =
	"SectionTLV.\"vtlv\" { scale { min \"-6000\" step \"50\" mute \"1\" } }\n"
	"SectionControlMixer.\"Master Playback Volume\" {\n"
	"  comment \"Q2 test\" index \"1\" channel.\"FL\" { reg \"0\" shift \"0\" } channel.\"FR\" { reg \"0\" shift \"8\" }\n"
	"  ops.\"ctl\" { info \"volsw\" get \"256\" put \"256\" } max \"255\" invert \"false\" tlv \"vtlv\"\n"
	"}\n"
	"SectionPCMCapabilities.\"cap\" { formats \"S16_LE,S24_LE,S32_LE\" rates \"44100,48000,96000\" channels_min \"2\" channels_max \"2\" }\n"
	"SectionPCM.\"pcm\" { index \"1\" id \"0\" dai.\"dai\" { id \"0\" } pcm.\"playback\" { capabilities \"cap\" } }\n";

int main(void)
{
	snd_tplg_t *t = snd_tplg_create(0);
	if (!t || snd_tplg_load(t, conf, sizeof conf - 1) < 0)
		return 2;
	char *text = NULL;
	if (snd_tplg_save(t, &text, 0) < 0)
		return 3;
	fputs(text, stdout);
	free(text);
	void *bin = NULL;
	size_t size = 0;
	if (snd_tplg_build_bin(t, &bin, &size) < 0)
		return 4;
	printf("--- binary %zu bytes\n", size);
	fwrite(bin, 1, size, stdout);
	free(bin);
	snd_tplg_free(t);
	return 0;
}
